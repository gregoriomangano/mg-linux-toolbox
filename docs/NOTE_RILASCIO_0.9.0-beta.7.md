# M.G Linux Toolbox 0.9.0 Beta 7

## Antivirus ClamAV

- Nuova sezione "Protezione malware" nella pagina Sicurezza: rilevamento reale dello stato (non installato / installato / pronto / firme da aggiornare), installazione dai soli repository ufficiali già configurati (Debian/Ubuntu, Fedora, openSUSE, Arch — nessun repository esterno), aggiornamento delle definizioni tramite freshclam, scansione on-demand di un file o di una cartella scelti dall'utente.
- Avvio e arresto del servizio di scansione (clamd) sono offerti soltanto quando il sistema espone realmente un'unit systemd gestibile; nessun nome di servizio viene assunto per la distribuzione.
- Disinstallazione multipiattaforma con conferma esplicita: rimuove soltanto i pacchetti ClamAV rilevati come effettivamente installati, mai un elenco fisso, e non tocca la configurazione dei repository.
- ClamAV non viene mai descritto come protezione in tempo reale: ogni stato mostrato riflette esattamente ciò che è stato rilevato.

## Rete e dispositivi / Sicurezza

- La pagina "Rete e sicurezza" è stata divisa in due pagine separate: **Rete e dispositivi** (connettività e condivisione) e **Sicurezza** (protezione del sistema, accesso, protezione malware).
- Firewall, Server SSH e Antivirus ClamAV sono ora nella pagina Sicurezza, insieme ad Aggiornamenti automatici, AppArmor/SELinux e Secure Boot.

## Aiuto e supporto

- Nuova pagina raggiungibile dalla barra superiore con i riferimenti di contatto diretti del progetto.

## Interfaccia

- Uniformato lo stile delle card in Funzioni kernel, Audio, Virtualizzazione, Energia e Servizi.
- La pagina Servizi mostra ogni servizio come card visibile distinta.
- Rimosso un riquadro informativo ridondante dalla pagina Software e repository.

## Limiti del collaudo

La candidata viene preparata per il collaudo manuale del proprietario del progetto. Le funzioni del Toolbox non sono state provate in macchine virtuali.
