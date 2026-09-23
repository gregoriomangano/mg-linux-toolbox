use futures_util::future::join_all;
use serde::{Deserialize, Serialize};
use std::{
    fs,
    path::PathBuf,
    sync::{
        atomic::{AtomicBool, AtomicU64, Ordering},
        Mutex,
    },
    time::{Duration, SystemTime, UNIX_EPOCH},
};
use tauri::{AppHandle, State};
use tauri_plugin_opener::OpenerExt;
use url::Url;

const MAX_BODY_BYTES: usize = 2 * 1024 * 1024;
const MAX_FEEDS: usize = 3;
const MAX_ARTICLES_PER_FEED: usize = 2;
pub const RSS_REFRESH_SECONDS: u64 = 60 * 60;
static NEXT_ID: AtomicU64 = AtomicU64::new(1);

#[derive(Clone, Debug, Deserialize, Serialize, PartialEq, Eq)]
#[serde(rename_all = "camelCase")]
pub struct FeedConfig {
    pub id: String,
    pub name: String,
    pub url: String,
}

#[derive(Clone, Debug, Deserialize, Serialize, PartialEq, Eq)]
#[serde(rename_all = "camelCase")]
pub struct Article {
    pub title: String,
    pub source: String,
    pub url: String,
    pub published_at: Option<String>,
    pub description: Option<String>,
}

#[derive(Clone, Debug, Deserialize, Serialize, PartialEq, Eq)]
#[serde(rename_all = "camelCase")]
pub struct FeedError {
    pub feed_id: String,
    pub feed_name: String,
    pub code: String,
}

#[derive(Clone, Debug, Deserialize, Serialize, PartialEq, Eq)]
#[serde(rename_all = "camelCase")]
pub struct RefreshResult {
    pub articles: Vec<Article>,
    pub errors: Vec<FeedError>,
    pub refreshed_at: u64,
}

#[derive(Deserialize, Serialize)]
struct FeedsFile {
    version: u8,
    feeds: Vec<FeedConfig>,
}

#[derive(Clone, Debug, Deserialize, Serialize)]
struct NewsCacheFile {
    version: u8,
    last_checked_at: u64,
    last_success_at: u64,
    result: RefreshResult,
}

pub struct FeedStore {
    path: PathBuf,
    cache_path: PathBuf,
    lock: Mutex<()>,
    cache_lock: Mutex<()>,
    refreshing: AtomicBool,
}

struct RefreshGuard<'a> {
    store: &'a FeedStore,
}

impl Drop for RefreshGuard<'_> {
    fn drop(&mut self) {
        self.store.refreshing.store(false, Ordering::Release);
    }
}

impl FeedStore {
    pub fn new(path: PathBuf) -> Self {
        let cache_path = path.with_file_name("news-cache.json");
        Self {
            path,
            cache_path,
            lock: Mutex::new(()),
            cache_lock: Mutex::new(()),
            refreshing: AtomicBool::new(false),
        }
    }

    pub fn read(&self) -> Result<Vec<FeedConfig>, String> {
        let _guard = self.lock.lock().map_err(|_| "feed store lock failed")?;
        self.read_unlocked()
    }

    fn read_unlocked(&self) -> Result<Vec<FeedConfig>, String> {
        if !self.path.exists() {
            return Ok(Vec::new());
        }
        let data = fs::read(&self.path).map_err(|e| e.to_string())?;
        let file: FeedsFile = serde_json::from_slice(&data).map_err(|e| e.to_string())?;
        match file.version {
            1 => Ok(file.feeds),
            _ => Err("unsupported feed configuration version".into()),
        }
    }

    #[cfg(test)]
    pub fn write(&self, feeds: &[FeedConfig]) -> Result<(), String> {
        let _guard = self.lock.lock().map_err(|_| "feed store lock failed")?;
        self.write_unlocked(feeds)
    }

    fn write_unlocked(&self, feeds: &[FeedConfig]) -> Result<(), String> {
        if let Some(parent) = self.path.parent() {
            fs::create_dir_all(parent).map_err(|e| e.to_string())?;
        }
        let temporary = self.path.with_extension("json.tmp");
        let data = serde_json::to_vec_pretty(&FeedsFile {
            version: 1,
            feeds: feeds.to_vec(),
        })
        .map_err(|e| e.to_string())?;
        fs::write(&temporary, data).map_err(|e| e.to_string())?;
        fs::rename(&temporary, &self.path).map_err(|e| e.to_string())
    }

    fn update(
        &self,
        change: impl FnOnce(&mut Vec<FeedConfig>) -> Result<(), String>,
    ) -> Result<Vec<FeedConfig>, String> {
        let _guard = self.lock.lock().map_err(|_| "feed store lock failed")?;
        let mut feeds = self.read_unlocked()?;
        change(&mut feeds)?;
        self.write_unlocked(&feeds)?;
        Ok(feeds)
    }

    fn read_cache(&self) -> Result<Option<NewsCacheFile>, String> {
        let _guard = self
            .cache_lock
            .lock()
            .map_err(|_| "news cache lock failed")?;
        if !self.cache_path.exists() {
            return Ok(None);
        }
        let bytes = fs::read(&self.cache_path).map_err(|error| error.to_string())?;
        let cache: NewsCacheFile =
            serde_json::from_slice(&bytes).map_err(|error| error.to_string())?;
        (cache.version == 1)
            .then_some(Some(cache))
            .ok_or_else(|| "unsupported news cache version".into())
    }

    fn write_cache(&self, cache: &NewsCacheFile) -> Result<(), String> {
        let _guard = self
            .cache_lock
            .lock()
            .map_err(|_| "news cache lock failed")?;
        if let Some(parent) = self.cache_path.parent() {
            fs::create_dir_all(parent).map_err(|error| error.to_string())?;
        }
        let temporary = self.cache_path.with_extension("json.tmp");
        fs::write(
            &temporary,
            serde_json::to_vec_pretty(cache).map_err(|error| error.to_string())?,
        )
        .map_err(|error| error.to_string())?;
        fs::rename(&temporary, &self.cache_path).map_err(|error| error.to_string())
    }

    fn begin_refresh_guard(&self) -> Option<RefreshGuard<'_>> {
        (!self.refreshing.swap(true, Ordering::AcqRel)).then_some(RefreshGuard { store: self })
    }
}

fn cache_matches_feeds(cache: &NewsCacheFile, feeds: &[FeedConfig]) -> bool {
    feeds.iter().all(|feed| {
        cache
            .result
            .articles
            .iter()
            .any(|article| article.source == feed.name)
            || cache
                .result
                .errors
                .iter()
                .any(|error| error.feed_id == feed.id)
    })
}

pub fn validate_feed_url(value: &str) -> Result<Url, String> {
    let url = Url::parse(value.trim()).map_err(|_| "invalid URL".to_string())?;
    if matches!(url.scheme(), "http" | "https") && url.host().is_some() {
        Ok(url)
    } else {
        Err("only HTTP and HTTPS URLs are accepted".into())
    }
}

fn normalize_name(value: &str) -> Result<String, String> {
    let value = value.trim();
    if value.is_empty() || value.chars().count() > 80 {
        Err("feed name must contain 1 to 80 characters".into())
    } else {
        Ok(value.to_string())
    }
}

fn new_id() -> String {
    let now = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or_default()
        .as_millis();
    format!("{now}-{}", NEXT_ID.fetch_add(1, Ordering::Relaxed))
}

pub fn parse_feed_document(bytes: &[u8], source: &str) -> Result<Vec<Article>, String> {
    let feed = feed_rs::parser::parse(normalize_xml_encoding(bytes)?.as_bytes())
        .map_err(|_| "invalid_feed".to_string())?;
    Ok(feed
        .entries
        .into_iter()
        .filter_map(|entry| {
            let title = entry.title?.content.trim().to_string();
            if title.is_empty() {
                return None;
            }
            let url = entry
                .links
                .into_iter()
                .map(|link| link.href)
                .find(|href| validate_feed_url(href).is_ok())?;
            let published_at = entry
                .published
                .or(entry.updated)
                .map(|date| date.to_rfc3339());
            let description = entry
                .summary
                .map(|content| content.content.trim().chars().take(280).collect::<String>())
                .filter(|content| !content.is_empty());
            Some(Article {
                title,
                source: source.to_string(),
                url,
                published_at,
                description,
            })
        })
        .collect())
}

fn normalize_xml_encoding(bytes: &[u8]) -> Result<String, String> {
    let declaration = String::from_utf8_lossy(&bytes[..bytes.len().min(512)]);
    let encoding = declaration
        .split("encoding=")
        .nth(1)
        .and_then(|value| {
            value
                .trim_start()
                .chars()
                .next()
                .map(|quote| (quote, value))
        })
        .and_then(|(quote, value)| value[1..].split(quote).next())
        .and_then(|label| encoding_rs::Encoding::for_label(label.trim().as_bytes()));
    match encoding {
        Some(encoding) => {
            let (decoded, _, had_errors) = encoding.decode(bytes);
            if had_errors {
                return Err("invalid_feed".into());
            }
            let mut decoded = decoded.into_owned();
            if let Some(start) = decoded.find("encoding=") {
                let value = &decoded[start + "encoding=".len()..];
                if let Some(quote) = value.chars().next() {
                    if let Some(end) = value[quote.len_utf8()..].find(quote) {
                        decoded.replace_range(
                            start
                                ..start
                                    + "encoding=".len()
                                    + quote.len_utf8()
                                    + end
                                    + quote.len_utf8(),
                            "encoding=\"UTF-8\"",
                        );
                    }
                }
            }
            Ok(decoded)
        }
        None => String::from_utf8(bytes.to_vec()).map_err(|_| "invalid_feed".to_string()),
    }
}

pub fn merge_feed_results(
    results: Vec<(FeedConfig, Result<Vec<Article>, String>)>,
) -> RefreshResult {
    let mut articles = Vec::new();
    let mut errors = Vec::new();
    for (feed, result) in results.into_iter().take(MAX_FEEDS) {
        match result {
            Ok(mut found) => {
                found.sort_by(|a, b| b.published_at.cmp(&a.published_at));
                let mut identities = std::collections::HashSet::new();
                articles.extend(
                    found
                        .into_iter()
                        .filter(|article| {
                            let identity = format!(
                                "{}\u{0}{}",
                                article.url,
                                article.title.trim().to_ascii_lowercase()
                            );
                            identities.insert(identity)
                        })
                        .take(MAX_ARTICLES_PER_FEED),
                );
            }
            Err(code) => errors.push(FeedError {
                feed_id: feed.id,
                feed_name: feed.name,
                code,
            }),
        }
    }
    RefreshResult {
        articles,
        errors,
        refreshed_at: SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .unwrap_or_default()
            .as_secs(),
    }
}

fn request_error(error: &reqwest::Error) -> String {
    if error.is_timeout() {
        "timeout".into()
    } else if error.is_redirect() {
        "redirect_error".into()
    } else if error
        .to_string()
        .to_ascii_lowercase()
        .contains("certificate")
        || error.to_string().to_ascii_lowercase().contains("tls")
    {
        "tls_error".into()
    } else {
        "network_error".into()
    }
}

async fn fetch_feed(
    client: reqwest::Client,
    feed: FeedConfig,
) -> (FeedConfig, Result<Vec<Article>, String>) {
    let result = async {
        validate_feed_url(&feed.url)?;
        let mut response = client
            .get(&feed.url)
            .send()
            .await
            .map_err(|error| request_error(&error))?;
        if !response.status().is_success() {
            return Err(format!("http_status_{}", response.status().as_u16()));
        }
        if response
            .headers()
            .get(reqwest::header::CONTENT_TYPE)
            .and_then(|value| value.to_str().ok())
            .is_some_and(|value| value.to_ascii_lowercase().starts_with("text/html"))
        {
            return Err("invalid_feed".into());
        }
        if response
            .content_length()
            .is_some_and(|length| length > MAX_BODY_BYTES as u64)
        {
            return Err("feed response is too large".into());
        }
        let mut body = Vec::new();
        while let Some(chunk) = response
            .chunk()
            .await
            .map_err(|error| request_error(&error))?
        {
            if body.len() + chunk.len() > MAX_BODY_BYTES {
                return Err("feed response is too large".into());
            }
            body.extend_from_slice(&chunk);
        }
        let articles = parse_feed_document(&body, &feed.name)?;
        (!articles.is_empty())
            .then_some(articles)
            .ok_or_else(|| "empty_feed".into())
    }
    .await;
    (feed, result)
}

#[tauri::command]
pub fn list_feeds(state: State<'_, FeedStore>) -> Result<Vec<FeedConfig>, String> {
    state.read()
}

#[tauri::command]
pub async fn save_feed(
    state: State<'_, FeedStore>,
    id: Option<String>,
    name: String,
    url: String,
) -> Result<Vec<FeedConfig>, String> {
    let url = validate_feed_url(&url)?.to_string();
    if id.as_ref().is_none_or(|id| id.is_empty()) && state.read()?.len() >= MAX_FEEDS {
        return Err("maximum_feeds_reached".into());
    }
    let client = reqwest::Client::builder()
        .timeout(Duration::from_secs(8))
        .user_agent(format!("M.G-Linux-Toolbox/{}", env!("CARGO_PKG_VERSION")))
        .build()
        .map_err(|_| "network_error".to_string())?;
    let (_, validation) = fetch_feed(
        client,
        FeedConfig {
            id: String::new(),
            name: String::new(),
            url: url.clone(),
        },
    )
    .await;
    validation?;
    let name = normalize_name(&name)?;
    state.update(|feeds| {
        if let Some(id) = id.filter(|id| !id.is_empty()) {
            let current = feeds
                .iter_mut()
                .find(|feed| feed.id == id)
                .ok_or("feed not found")?;
            current.name = name;
            current.url = url;
        } else {
            if feeds.len() >= MAX_FEEDS {
                return Err("maximum_feeds_reached".into());
            }
            if feeds.iter().any(|feed| feed.url == url) {
                return Err("feed already exists".into());
            }
            feeds.push(FeedConfig {
                id: new_id(),
                name,
                url,
            });
        }
        Ok(())
    })
}

#[tauri::command]
pub fn delete_feed(state: State<'_, FeedStore>, id: String) -> Result<Vec<FeedConfig>, String> {
    state.update(|feeds| {
        let before = feeds.len();
        feeds.retain(|feed| feed.id != id);
        (before != feeds.len())
            .then_some(())
            .ok_or_else(|| "feed not found".into())
    })
}

#[tauri::command]
pub async fn refresh_feeds(
    state: State<'_, FeedStore>,
    force: Option<bool>,
) -> Result<RefreshResult, String> {
    let now = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or_default()
        .as_secs();
    let feeds = state.read()?;
    if !force.unwrap_or(false) {
        if let Some(cache) = state.read_cache()? {
            if now.saturating_sub(cache.last_success_at) < RSS_REFRESH_SECONDS
                && cache_matches_feeds(&cache, &feeds)
            {
                return Ok(cache.result);
            }
        }
    }
    let Some(_refresh_guard) = state.begin_refresh_guard() else {
        return state
            .read_cache()?
            .map(|cache| cache.result)
            .ok_or_else(|| "feed refresh already running".into());
    };
    let client = reqwest::Client::builder()
        .timeout(Duration::from_secs(8))
        .user_agent(format!("M.G-Linux-Toolbox/{}", env!("CARGO_PKG_VERSION")))
        .build()
        .map_err(|e| e.to_string())?;
    let tasks = feeds
        .iter()
        .cloned()
        .map(|feed| fetch_feed(client.clone(), feed));
    let result = merge_feed_results(join_all(tasks).await);
    let previous = state.read_cache().ok().flatten();
    let fetched_any = !result.articles.is_empty();
    let mut cached = result;
    if let Some(previous) = previous.as_ref() {
        for failed in &cached.errors {
            for article in previous
                .result
                .articles
                .iter()
                .filter(|article| article.source == failed.feed_name)
            {
                if !cached
                    .articles
                    .iter()
                    .any(|current| current.url == article.url)
                {
                    cached.articles.push(article.clone());
                }
            }
        }
    }
    let cache = NewsCacheFile {
        version: 1,
        last_checked_at: now,
        last_success_at: if !fetched_any {
            previous
                .as_ref()
                .map(|value| value.last_success_at)
                .unwrap_or(0)
        } else {
            now
        },
        result: cached.clone(),
    };
    let _ = state.write_cache(&cache);
    Ok(cached)
}

#[tauri::command]
pub fn cached_news(state: State<'_, FeedStore>) -> Result<Option<RefreshResult>, String> {
    Ok(state.read_cache()?.map(|cache| cache.result))
}

#[tauri::command]
pub fn open_external_url(app: AppHandle, url: String) -> Result<(), String> {
    let url = validate_feed_url(&url)?.to_string();
    app.opener()
        .open_url(url, None::<&str>)
        .map_err(|e| e.to_string())
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::time::{SystemTime, UNIX_EPOCH};

    fn feed(id: &str, name: &str, url: &str) -> FeedConfig {
        FeedConfig {
            id: id.into(),
            name: name.into(),
            url: url.into(),
        }
    }

    #[test]
    fn validates_only_http_and_https_feed_urls() {
        assert!(validate_feed_url("https://example.test/feed.xml").is_ok());
        assert!(validate_feed_url("http://127.0.0.1:8080/rss").is_ok());
        assert!(validate_feed_url("file:///etc/passwd").is_err());
        assert!(validate_feed_url("javascript:alert(1)").is_err());
        assert!(validate_feed_url("not a url").is_err());
    }

    #[test]
    fn persists_at_least_three_feeds_across_store_instances() {
        let unique = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .unwrap()
            .as_nanos();
        let path = std::env::temp_dir().join(format!("mg-toolbox-feeds-{unique}.json"));
        let original = vec![
            feed("1", "One", "https://one.test/rss"),
            feed("2", "Two", "https://two.test/atom"),
            feed("3", "Three", "https://three.test/feed"),
        ];
        FeedStore::new(path.clone()).write(&original).unwrap();
        assert_eq!(FeedStore::new(path.clone()).read().unwrap(), original);
        let _ = std::fs::remove_file(path);
    }

    #[test]
    fn parses_rss_and_atom_without_rendering_remote_html() {
        let rss = br#"<?xml version="1.0"?><rss version="2.0"><channel><title>Linux News</title><item><title>Kernel ready</title><link>https://example.test/kernel</link><pubDate>Sun, 14 Sep 2026 08:00:00 GMT</pubDate><description><![CDATA[<b>ignored</b>]]></description></item></channel></rss>"#;
        let atom = br#"<?xml version="1.0"?><feed xmlns="http://www.w3.org/2005/Atom"><title>Open Source</title><entry><title>Release notes</title><link href="https://example.test/release"/><updated>2026-09-14T09:00:00Z</updated></entry></feed>"#;
        let a = parse_feed_document(rss, "RSS source").unwrap();
        let b = parse_feed_document(atom, "Atom source").unwrap();
        assert_eq!(
            (a[0].title.as_str(), a[0].source.as_str(), a[0].url.as_str()),
            ("Kernel ready", "RSS source", "https://example.test/kernel")
        );
        assert_eq!(
            (b[0].title.as_str(), b[0].source.as_str()),
            ("Release notes", "Atom source")
        );
    }

    #[test]
    fn parses_iso_8859_1_rss_documents() {
        let rss = b"<?xml version=\"1.0\" encoding=\"ISO-8859-1\"?><rss version=\"2.0\"><channel><item><title>Notizia caff\xe8</title><link>https://example.test/news</link></item></channel></rss>";
        let articles = parse_feed_document(rss, "ISO source").unwrap();
        assert_eq!(articles[0].title, "Notizia caffè");
    }

    #[test]
    fn broken_feed_does_not_discard_successful_feeds() {
        let first = Article {
            title: "One".into(),
            source: "A".into(),
            url: "https://a.test/1".into(),
            published_at: None,
            description: None,
        };
        let second = Article {
            title: "Two".into(),
            source: "B".into(),
            url: "https://b.test/2".into(),
            published_at: None,
            description: None,
        };
        let result = merge_feed_results(vec![
            (feed("1", "A", "https://a.test"), Ok(vec![first])),
            (
                feed("2", "Broken", "https://broken.test"),
                Err("invalid feed".into()),
            ),
            (feed("3", "B", "https://b.test"), Ok(vec![second])),
        ]);
        assert_eq!(result.articles.len(), 2);
        assert_eq!(result.errors.len(), 1);
        assert_eq!(result.errors[0].feed_name, "Broken");
    }

    #[test]
    fn overview_keeps_at_most_two_articles_per_feed() {
        let articles = |source: &str| {
            (1..=3)
                .map(|number| Article {
                    title: format!("{source} {number}"),
                    source: source.into(),
                    url: format!("https://{source}.test/{number}"),
                    published_at: Some(format!("2026-09-15T0{number}:00:00Z")),
                    description: None,
                })
                .collect::<Vec<_>>()
        };
        let result = merge_feed_results(vec![
            (
                feed("one", "One", "https://one.test/rss"),
                Ok(articles("one")),
            ),
            (
                feed("two", "Two", "https://two.test/rss"),
                Ok(articles("two")),
            ),
        ]);
        assert_eq!(result.articles.len(), 4);
        assert_eq!(
            result
                .articles
                .iter()
                .filter(|article| article.source == "one")
                .count(),
            2
        );
        assert_eq!(
            result
                .articles
                .iter()
                .filter(|article| article.source == "two")
                .count(),
            2
        );
    }

    #[test]
    fn refresh_gate_is_released_when_preparation_returns_early() {
        let path = std::env::temp_dir().join("mg-toolbox-refresh-guard.json");
        let store = FeedStore::new(path);
        let failed_preparation: Result<(), &str> = {
            let _guard = store
                .begin_refresh_guard()
                .expect("first refresh acquires the gate");
            Err("simulated client setup failure")
        };
        assert!(failed_preparation.is_err());
        assert!(
            store.begin_refresh_guard().is_some(),
            "an early return must not permanently block a later refresh"
        );
    }

    #[test]
    fn fresh_cache_is_used_only_for_the_current_feed_configuration() {
        let cache = NewsCacheFile {
            version: 1,
            last_checked_at: 1,
            last_success_at: 1,
            result: RefreshResult {
                articles: vec![Article {
                    title: "Ubuntu news".into(),
                    source: "Ubuntu".into(),
                    url: "https://ubuntu.com/blog/news".into(),
                    published_at: None,
                    description: None,
                }],
                errors: vec![],
                refreshed_at: 1,
            },
        };
        assert!(cache_matches_feeds(
            &cache,
            &[feed("ubuntu", "Ubuntu", "https://ubuntu.com/blog/feed")]
        ));
        assert!(!cache_matches_feeds(
            &cache,
            &[feed(
                "arch",
                "Arch Linux",
                "https://archlinux.org/feeds/news/"
            )]
        ));
    }

    #[test]
    fn refresh_interval_is_one_hour() {
        assert_eq!(RSS_REFRESH_SECONDS, 3600);
    }

    #[test]
    #[ignore = "requires live official RSS endpoints; run manually with -- --ignored --nocapture"]
    fn live_official_feeds_parse_end_to_end() {
        let client = reqwest::Client::builder()
            .timeout(Duration::from_secs(20))
            .user_agent(format!("M.G-Linux-Toolbox/{}", env!("CARGO_PKG_VERSION")))
            .build()
            .unwrap();
        let feeds = [
            ("Ubuntu", "https://ubuntu.com/blog/feed"),
            ("Linux Mint", "https://blog.linuxmint.com/?feed=rss2"),
            ("Arch Linux", "https://archlinux.org/feeds/news/"),
            (
                "Hardware Upgrade",
                "https://feeds.hwupgrade.it/rss_hwup.xml",
            ),
            ("Windows Blog", "https://blogs.windows.com/feed/"),
        ];
        tauri::async_runtime::block_on(async {
            for (name, url) in feeds {
                let (_, result) = fetch_feed(client.clone(), feed(name, name, url)).await;
                let articles = result.unwrap_or_else(|error| panic!("{name}: {error}"));
                let first = &articles[0];
                eprintln!(
                    "{name}: {} | {:?} | {}",
                    first.title, first.published_at, first.url
                );
            }
        });
    }
}
