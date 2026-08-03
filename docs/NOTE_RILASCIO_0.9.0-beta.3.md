# M.G Linux Toolbox 0.9.0 Beta 3

## In evidenza

Questa Beta 3 corregge la lettura visiva della pressione PSI del disco, aggiunge una pagina di attività live e introduce la prima versione del Gaming Pack in modalità esclusivamente informativa.

## Modifiche principali

- Il valore PSI del disco indica il tempo in cui i programmi restano in attesa di operazioni I/O: non è una percentuale di utilizzo e, da solo, non indica un guasto.
- `avg10` rappresenta il segnale corrente principale, `avg60` lo conferma e `avg300` viene mostrato soltanto come dato storico/tecnico.
- Lo stato PSI richiede due campioni coerenti per cambiare e torna automaticamente normale quando la pressione termina.
- La nuova pagina **Attività del disco** mostra velocità reali dei dispositivi e programmi con maggiori letture o scritture usando `/proc` e `/sys`.
- Il campionamento dei processi viene eseguito fuori dal thread dell'interfaccia e si ferma quando la pagina non è attiva.
- Il **Gaming Pack V1** rileva distribuzione, GPU, pacchetti già presenti e pacchetti eventualmente disponibili nei repository già configurati.

## Limiti del Gaming Pack V1

Il Gaming Pack è una prima versione di sola analisi e anteprima. Non installa né rimuove pacchetti, non abilita repository, non aggiunge RPM Fusion, AUR, multiverse, multilib o Non-OSS, non installa driver, non cambia kernel e non esegue aggiornamenti completi del sistema.

La mappatura Debian/Ubuntu/Mint/Pop!_OS è stata verificata sulla macchina di collaudo Pop!_OS 24.04. Le mappature Fedora, Arch/Manjaro/EndeavourOS/CachyOS e openSUSE Leap/Tumbleweed sono prudenti e basate sugli indici ufficiali, ma richiedono ancora prove su macchine reali.

## File e checksum

- File: `MG-Linux-Toolbox-0.9.0-beta.3-x86_64.AppImage`
- SHA-256: `cccc1de52960d6cfa9c679acaf805f16afd50a3e5c75a9e8548438dd7a1e95dc`

## Installazione manuale

1. Scaricare `MG-Linux-Toolbox-0.9.0-beta.3-x86_64.AppImage` e il file `.sha256` dalla stessa Release.
2. Verificare il checksum SHA-256.
3. Rendere eseguibile l'AppImage dalle proprietà del file oppure con:

   ```bash
   chmod +x "MG-Linux-Toolbox-0.9.0-beta.3-x86_64.AppImage"
   ```

4. Avviarla con un doppio clic oppure con:

   ```bash
   ./"MG-Linux-Toolbox-0.9.0-beta.3-x86_64.AppImage"
   ```

L'AppImage è leggera e usa Python 3, PyGObject, GTK4, Libadwaita e FUSE forniti dal sistema host.

## Aggiornamento dalla Beta 2

La Beta 2 resta una release storica separata. Per un'AppImage usata manualmente, chiudere la Beta 2, scaricare e verificare la Beta 3 e avviare il nuovo file; eliminare la vecchia AppImage soltanto dopo aver verificato il nuovo avvio.

Chi usa l'installazione automatica può rieseguire `install.sh`: il checksum viene controllato prima della sostituzione e la versione precedente viene conservata temporaneamente come backup. Cronologia, impostazioni e punti di ripristino rimangono invariati.

## Collaudo

La suite completa comprende 932 test superati. L'ambiente reale di preparazione è Pop!_OS 24.04 LTS; la compatibilità con altre distribuzioni deve ancora essere verificata direttamente.
