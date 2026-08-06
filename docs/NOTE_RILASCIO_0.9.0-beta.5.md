# M.G Linux Toolbox 0.9.0 Beta 5

## Correzioni dell'aggiornamento gestito

- La nuova AppImage viene sostituita soltanto dopo il controllo SHA-256.
- Un helper esterno attende la conferma atomica dell'avvio della nuova versione.
- Se la conferma manca, il processo termina o il periodo di stabilizzazione fallisce, la versione precedente viene ripristinata atomicamente e verificata.
- La copia precedente resta pendente fino alla conferma dell'avvio e viene eliminata soltanto dopo la finalizzazione del backup.
- Gli errori di avvio, rollback e notifica vengono registrati nel log tecnico dell'installazione gestita.

## Conversione da AppImage portatile

- La copia viene verificata per dimensione, permessi e SHA-256 prima della sostituzione.
- Il launcher viene scritto in un file temporaneo e sostituito atomicamente.
- Se la conversione fallisce, la destinazione gestita e il launcher precedente restano invariati.

## Limiti del collaudo

Questa candidata è preparata per il collaudo manuale del proprietario del progetto. Le funzioni del Toolbox non sono state provate in macchine virtuali.
