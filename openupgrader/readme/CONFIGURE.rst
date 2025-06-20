Questo modulo funziona con una serie di librerie presenti normalmente in ambiente Debian/Ubuntu, con alcune aggiunte, una configurazione docker utilizzabile di esempio è qui: https://github.com/kenayagi/docker-odoo/blob/14.0/Dockerfile

#### ISTRUZIONI DA AGGIORNARE ####
La migrazione viene eseguita con i seguenti passaggi, impostando:

#. crea virtualenv: settare a True, a meno che non siano già stati creati per evitarne la ri-creazione
#. crea backup e aggiorna: settare a True per partire da zero oppure per ripulire una migrazione non riuscita, altrimenti settare a False e:
#. ripristina il backup eseguito in precedenza: in alternativa alla voce sopra

È necessario avviare il server in modalità singolo worker (workers=0) e impostare limiti cpu adeguatamente alti.
Un possibile futuro sviluppo sarà l'utilizzo di queue_job dalle versioni in cui è disponibile.

Migrazione:

#. Creare la versione di Odoo su cui l'istanza è attiva e per la versione successiva
#. Creare o importare i repository con il bottone... per ognuna delle versioni create
#. Creare o importare la configurazione con il bottone... per ognuna delle versioni create
#. Creare la configurazione per la migrazione dalla versione attuale alla versione successiva
#. Creare i virtualenv con il bottone...
#. Avviare la migrazione alla versione successiva con il bottone...
#. Nelle cartelle ./data_dir/openupgrade/<nome del database>/openupgrade<versione>/ si troverà l'istanza alla versione attuale aggiornata, e per ogni versione successiva migrata, con le varie versioni del database, del filestore e dei requirements e (tra l'altro) il log della migrazione della specifica versione.
#. n.b. è possibile ripartire da una migrazione in corso da una qualsiasi versione intermedia se non è riuscita, impostando il flag restore_db_only prima di riavviare con il bottone..., che ripristina il db migrato alla versione iniziale indicata, mentre restore_db_update fa inoltre l'aggiornamento con -u all
