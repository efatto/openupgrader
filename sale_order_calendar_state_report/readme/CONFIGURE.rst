Questo modulo permette di visualizzare i dati relativi alle ore previste e quelle effettive (con i costi previsti ed effettivi) degli ordini di lavoro, con le quantità previste nelle distinte base e quelle effettive nell'ordine di produzione (e i corrispondenti costi previsti ed effettivi) in un'unica statistica.

Nella produzione è disponibile questo menu:

.. image:: ../static/description/menu.png
    :alt: Menu

da cui è possibile visualizzare dati riassuntivi relativi ai tempi, materiali
e costi degli ordini di produzione:

.. image:: ../static/description/statistica.png
    :alt: Statistica

È inoltre disponibile un link diretto al report dall'Ordine di produzione:

.. image:: ../static/description/link_diretto_statistica.png
    :alt: Link diretto statistica

Note:
#. se il componente non è presente nella DiBa a video (nel caso sia stato modificato oppure sia parte di un kit) la quantità prevista e il costo previsto sono relativi alla quantità visibile a video nella produzione. Non rileva quindi la previsione iniziale nella DiBa principale o nelle DiBa figlie. In questi casi, la quantità a video sarà quindi indicata come quantità prevista, anche se inizialmente era diversa, essendo tale valore calcolato e non disponibile.
#. i costi dei tempi lavorati sono valorizzati al costo del centro di lavoro collegato
#. i costi dei materiali sono valorizzati al costo registrato nel movimento di magazzino di scarico in base alla configurazione del sistema al momento del completamento della produzione.

### NOTE PER SVILUPPO
1. avere il costo di produzione con i costi in data della produzione (per costificare una commessa anche dopo vari acquisti della materia prima) SISTEMARE I COSTI che adesso sono quelli del momento in cui viene completata la produzione, aggiornandoli con quelli dopo l'ingresso degli acquisti collegati, ma non i costi attuali. Come aggiorno i costi se li conosco dopo che è stata consolidata? Gli articoli che vedi sottolineati, l'IRON su tutti, sono stati acquistati dopo la conferma della produzione e abbiamo conosciuto il costo solo dalla fattura... Il bottone aggiorna il costo sul movimento in ingresso pensandoci, questo è il costo sul movimento di scarico
2. averlo con i costi aggiornati alla data della stampa del report (per valutare il costo di un prodotto che magari produco poco prima di fare una quotazione di vendita)
3. visualizzare in rosso che ci sono zeri, altrimenti possono sfuggire falsando i totali
4. creare due report separati mostrando i campi pertinenti per avere con comodità i 2 tipi di valutazione

per risolvere potrei:
BOCCIATO A. creare un cron che vada a prendere le righe dei componenti delle produzioni (con costo 0 - in alternativa tutte) e aggiornarle con l'ultimo costo di acquisto - in alternativa fare un calcolo di quale era il costo dell'articolo consumato (ad es. un articolo era già in magazzino ma è stato acquistato nel frattempo);
APPROVATO B. creare un wizard lanciabile da ogni singola produzione che fa le stesse cose sopra, in questo caso si potrebbe anche esporre all'utente una tabella in cui vedere le differenze e altre info specifiche
BOCCIATO quindi diresti di mettere il tutto nello stesso wizard? (modifica/ricalcola prezzi e poi apri il report?): Non credo che il report sia il punto giusto per fare modifiche ai costi nel DB in modo permanente, da dove fare l'aggiornamento di questi campi lo lascerei decidere ai tecnici.

DA APPROVARE: aggiungere una colonna con il costo di sostituzione (qui dipende da come andrete a gestire i costi, se il costo prodotto sarà sempre il costo ultimo allora basta questo)
DA FARE se ci sono valori a zero direi di evidenziarli, come suggerito la settimana scorsa.
