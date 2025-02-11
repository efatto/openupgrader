Questo modulo aggiunge al lead del CRM:

#. Prodotto
#. Quantità annuale stimata

Questi campi vengono utilizzati, insieme alla Probabilità, per aggiungere alla
regola di riordino del prodotto una quantità aggiuntiva variabile in base al
parametro `crm.product.lead.probability.list` nella configurazione.

Per esempio, impostando [(30, 10), (50, 20), (70, 100)] con una
Quantità stimata venduta di 500 pezzi, verrà aggiunto alla quantità minima e
massima della prima regola di riordino il valore:

#. con probabilità > 30 e inferiore a 50: (500 * 10 / 100) = 50
#. con probabilità > 50 e inferiore a 70: (500 * 20 / 100) = 100
#. con probabilità > 70: (500 * 100 / 100) = 500

Sono esclusi da queste variazioni i prodotti appartenenti ad una categoria con il flag `È speciale`.

Il valore di default del parametro è: [(50, 20), (100, 100)]
