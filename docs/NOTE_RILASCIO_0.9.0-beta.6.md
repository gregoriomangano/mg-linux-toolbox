# M.G Linux Toolbox 0.9.0 Beta 6

## Correzione urgente dell'aggiornamento gestito

- L'AppImage scaricata viene copiata in un file temporaneo creato direttamente nella directory dell'installazione gestita prima della sostituzione atomica.
- La copia viene verificata per presenza, tipo regolare, dimensione, permesso eseguibile e SHA-256, quindi sincronizzata su disco.
- La versione installata resta invariata se la preparazione della copia fallisce.
- Il backup e il rollback restano sul filesystem della directory gestita.

## Limiti del collaudo

La candidata viene preparata per il collaudo manuale del proprietario del progetto. Le funzioni del Toolbox non sono state provate in macchine virtuali.
