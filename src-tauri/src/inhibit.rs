use serde::Serialize;
use std::{collections::HashMap, sync::Mutex};
use zbus::{
    blocking::{Connection, Proxy},
    zvariant::{OwnedObjectPath, Value},
};

const PORTAL_DESTINATION: &str = "org.freedesktop.portal.Desktop";
const PORTAL_PATH: &str = "/org/freedesktop/portal/desktop";
const INHIBIT_FLAGS: u32 = 4 | 8; // suspend + idle, as defined by the XDG portal.

struct PortalInhibitor {
    _connection: Connection,
}

#[derive(Default)]
pub struct InhibitState {
    active: Mutex<Option<PortalInhibitor>>,
}

#[derive(Serialize)]
#[serde(rename_all = "camelCase")]
pub struct InhibitStatus {
    pub available: bool,
    pub active: bool,
}

fn portal_supports_inhibit(connection: &Connection) -> Result<bool, String> {
    let proxy = Proxy::new(
        connection,
        PORTAL_DESTINATION,
        PORTAL_PATH,
        "org.freedesktop.DBus.Introspectable",
    )
    .map_err(|_| "inhibit_unavailable".to_string())?;
    let xml: String = proxy
        .call("Introspect", &())
        .map_err(|_| "inhibit_unavailable".to_string())?;
    Ok(xml.contains("org.freedesktop.portal.Inhibit"))
}

fn portal_connection() -> Result<Connection, String> {
    let connection = Connection::session().map_err(|_| "inhibit_unavailable".to_string())?;
    if portal_supports_inhibit(&connection)? {
        Ok(connection)
    } else {
        Err("inhibit_unavailable".into())
    }
}

impl InhibitState {
    pub fn status(&self) -> InhibitStatus {
        let active = self
            .active
            .lock()
            .map(|value| value.is_some())
            .unwrap_or(false);
        let available = active || portal_connection().is_ok();
        InhibitStatus { available, active }
    }

    pub fn set_active(&self, requested: bool) -> Result<InhibitStatus, String> {
        let mut active = self
            .active
            .lock()
            .map_err(|_| "inhibit_unavailable".to_string())?;
        if requested {
            if active.is_some() {
                return Ok(InhibitStatus {
                    available: true,
                    active: true,
                });
            }
            let connection = portal_connection()?;
            let proxy = Proxy::new(
                &connection,
                PORTAL_DESTINATION,
                PORTAL_PATH,
                "org.freedesktop.portal.Inhibit",
            )
            .map_err(|_| "inhibit_unavailable".to_string())?;
            let mut options = HashMap::<&str, Value<'static>>::new();
            options.insert(
                "reason",
                Value::from("M.G Linux Toolbox mantiene il computer attivo"),
            );
            let _: OwnedObjectPath = proxy
                .call("Inhibit", &("", INHIBIT_FLAGS, options))
                .map_err(|_| "inhibit_request_failed".to_string())?;
            // The portal completes the request immediately. Its inhibition is tied to the
            // caller's D-Bus connection, so retaining this dedicated connection keeps it
            // active and dropping it releases it again.
            *active = Some(PortalInhibitor {
                _connection: connection,
            });
            return Ok(InhibitStatus {
                available: true,
                active: true,
            });
        }

        // XDG Portal Inhibit has no persistent request object after it replied. Dropping the
        // dedicated connection is the documented lifetime boundary for this inhibition.
        drop(active.take());
        Ok(InhibitStatus {
            available: portal_connection().is_ok(),
            active: false,
        })
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn portal_capability_is_safe_when_the_session_bus_is_missing() {
        let status = InhibitState::default().status();
        assert!(!status.active);
    }

    #[test]
    #[ignore = "requires a real desktop portal session"]
    fn portal_inhibit_round_trip() {
        let state = InhibitState::default();
        if !state.status().available {
            return;
        }
        assert!(state.set_active(true).expect("portal activation").active);
        assert!(!state.set_active(false).expect("portal release").active);
    }
}
