# Risoluzione dei problemi { #troubleshooting }

Parti dalla tabella: trova il tuo problema, poi segui il collegamento per i dettagli.

| Problema | Cosa controllare |
|---|---|
| La configurazione viene rifiutata | Il messaggio nel modulo: vedi [Perché la configurazione può essere rifiutata](setup.md#why-the-setup-can-be-refused). |
| Home Assistant non riesce a collegarsi | Indirizzo, porta, rete, firewall: vedi [Connessione impossibile](#cannot-connect). |
| **Cerca nella rete** non trova nessuna scheda | Stessa sottorete, porte UDP libere: vedi [La ricerca non trova nessuna scheda](#search-finds-no-board). |
| Un comando non riesce, con un messaggio | Il messaggio dice perché: vedi [Quando la centrale rifiuta un comando](#when-the-panel-refuses-a-command). |
| L'inserimento viene rifiutato perché ci sono zone non pronte | Chiudi o escludi le zone, oppure inserisci comunque: vedi [Zone non pronte](actions-events.md#zones-not-ready). |
| I comandi non riescono con "la centrale è in programmazione" | Attendi il termine della programmazione: vedi [Modalità programmazione](#programming-mode). |
| Nomi, zone o scenari non sono aggiornati | Usa **Rileggi la configurazione della centrale**: vedi [Configurazione](setup.md#re-reading-the-panel-configuration). |
| Entità mancanti, in eccesso o non aggiornate | Vedi [Entità mancanti o non aggiornate](#entities-missing-or-not-updating). |
| L'app Inim o il software dell'installatore non riescono a collegarsi in locale | Attiva la modalità aggiornamento lento: vedi [L'app Inim](#the-inim-app-cannot-connect-locally). |
| Entità non disponibili per un po' dopo una modifica alla programmazione della centrale | Vedi [Dopo una modifica alla programmazione della centrale](#after-the-panels-programming-was-changed). |
| Segnalare un problema | Allega un [report di sistema](system-report.md) alla issue su GitHub e, se puoi, un [log di debug](#debug-log). |

## Quando la centrale rifiuta un comando { #when-the-panel-refuses-a-command }

L'inserimento, il disinserimento, l'esclusione di una zona, l'attivazione di uno scenario, il reset delle memorie allarmi, il comando di un'uscita e la modifica di un termostato non riescono, con un messaggio, quando non vengono eseguiti. Il messaggio dice quale di tre cose è successa.

**1. L'integrazione non ha inviato il comando.**

*   Prima di inserire o attivare uno scenario, l'integrazione controlla le zone (vedi [Zone non pronte](actions-events.md#zones-not-ready)). Se alcune non sono pronte e l'opzione dice *Rifiuta*, il messaggio le elenca e alla centrale non viene inviato nulla.
*   Mentre la centrale è in programmazione ignora i comandi, quindi non ne viene inviato nessuno (*"Il comando per Area 3 non è stato inviato: la centrale è in programmazione. Riprova al termine della programmazione."*). Se la centrale entra in programmazione mentre un comando viene inviato, il messaggio dice che il comando potrebbe non essere stato eseguito: controlla l'area o la zona, e riprova al termine della programmazione.

**2. La centrale l'ha rifiutato.** Il messaggio dà il motivo della centrale:

| Messaggio | Motivo negli eventi | Cosa fare |
|---|---|---|
| La centrale non ha accettato il PIN | `invalid_code` | Il codice utente è stato cambiato o cancellato sulla centrale: inserisci il PIN attuale con **Riconfigura**. |
| Il PIN non è abilitato | `code_not_enabled` | Il codice utente non comanda quell'area: abilitalo sulla centrale o usa un altro codice. |
| Non consente l'inserimento con zone non pronte | `open_zones` | Chiudi o escludi le zone aperte dell'area, poi inserisci di nuovo. |
| La zona non può essere esclusa | `cannot_be_bypassed` | La programmazione della zona sulla centrale non lo consente. |
| La zona non appartiene a nessuna area | `no_areas` | La programmazione della zona sulla centrale non lo consente. |
| Errore di zona doppia | `double_zone_error` | La programmazione della zona sulla centrale non lo consente. |
| È già nello stato richiesto | `already_in_state` | Non c'è nulla da fare: la zona o l'area era già come richiesto. |
| Il terminale è fuori intervallo | `slot_out_of_range` | Il terminale della zona o dell'uscita è cambiato sulla centrale: usa **Rileggi la configurazione della centrale**. |
| Il terminale non è collegato | `slot_not_connected` | Come sopra. |
| Il terminale non è utilizzato | `null_terminal` | Come sopra. |
| L'uscita non è abilitata | `output_not_enabled` | Il tuo codice utente non può comandare quell'uscita: abilitala per il codice sulla centrale, oppure deseleziona l'uscita. |
| L'uscita è bloccata mentre le sue aree sono inserite | `areas_armed` | La centrale non lascia cambiare l'uscita mentre le sue aree sono inserite: prima disinseriscile. |
| La centrale ha rifiutato il comando | `refused` | La centrale non ha dato un motivo. |
| La centrale ha rifiutato il comando (codice di risultato N) | `unknown` | La centrale ha dato un motivo che l'integrazione non conosce ancora. |

**3. Il comando non è stato completato.** La centrale non era raggiungibile, oppure la sua risposta non si poteva leggere. Vedi [Connessione impossibile](#cannot-connect); riprova quando la connessione è tornata.

Uno scenario non riuscito mostra anche una notifica e genera l'evento [`inim_smartliving_alarm_scenario_activation_failed`](actions-events.md#event-scenario-activation-failed), con il motivo.

## Problemi comuni { #common-problems }

### Connessione impossibile { #cannot-connect }

*   Verifica l'indirizzo IP e la porta della tua scheda SmartLAN.
*   Assicurati che Home Assistant possa raggiungere l'indirizzo della scheda su quella porta (controlla firewall e segmentazione della rete).
*   Assicurati che la scheda sia alimentata e collegata alla rete.
*   Se l'indirizzo o la porta della scheda sono cambiati, aggiornali con **Riconfigura** (vedi [Cambiare i dati di connessione](setup.md#changing-the-connection-details-reconfigure)) invece di rimuovere l'integrazione.
*   Se il nome utente o la password di una scheda SmartLAN/G sono cambiati, **Rileggi la configurazione della centrale** dice che la scheda non li ha accettati: inserisci quelli attuali con **Riconfigura** (vedi [Cambiare i dati di connessione](setup.md#changing-the-connection-details-reconfigure)).

### La ricerca non trova nessuna scheda { #search-finds-no-board }

*   La ricerca raggiunge solo le schede nella stessa sottorete di Home Assistant (le schede rispondono solo sul proprio segmento di rete). Inserisci a mano una scheda in un'altra sottorete o VLAN.
*   La ricerca ascolta sulle porte UDP 5001 (SmartLAN/SI) e 7001 (SmartLAN/G) dell'host di Home Assistant; un altro programma che le usa, o un firewall, la impedisce.
*   Le schede già configurate non vengono elencate.

### SmartLAN/G: la risposta non è nel formato previsto { #smartlang-the-reply-was-not-in-the-expected-format }

La configurazione dice *"La scheda SmartLAN/G ha risposto, ma la risposta non è nel formato previsto"*. Controlla prima il nome utente e la password della scheda. Se sono corretti, probabilmente la tua scheda risponde in un modo che questa integrazione non gestisce ancora. Apri una issue su GitHub con il modello e il firmware della centrale, la versione della scheda e un [log di debug](#debug-log) di un tentativo di configurazione.

### L'app Inim non riesce a collegarsi in locale { #the-inim-app-cannot-connect-locally }

La scheda SmartLAN/SI accetta una sola connessione locale alla volta. Mentre Home Assistant legge la centrale agli intervalli di [Aggiornamento](options.md#polling), tiene aperta la connessione alla scheda, quindi nel frattempo l'app non riesce a collegarsi in locale. L'accesso remoto tramite Inim Cloud non ne risente.

Attiva **Modalità aggiornamento lento** nella pagina del dispositivo della centrale (vedi [Modalità aggiornamento lento](options.md#slow-polling-mode)). Home Assistant legge allora la centrale ogni 30 secondi per impostazione predefinita (**Intervallo di lettura in modalità aggiornamento lento**, in [Aggiornamento](options.md#polling)) e chiude la connessione dopo ogni lettura, così l'app, o il software dell'installatore, possono collegarsi nel frattempo. Mentre un'altra app è collegata, le entità non sono disponibili; mentre il software dell'installatore legge la programmazione della centrale, questo può durare circa un minuto. Se il software dell'installatore segnala un errore di connessione, riprova: la lettura di Home Assistant potrebbe aver occupato la scheda per un momento. La modalità si disattiva da sola dopo 60 minuti, se non imposti diversamente.

### Modalità programmazione { #programming-mode }

*   Qualcuno sta programmando la centrale da una tastiera, oppure la centrale sta ricaricando la sua configurazione. I comandi funzionano di nuovo al termine della programmazione; il sensore *Modalità programmazione* mostra quando.
*   La programmazione dal software dell'installatore su un PC non blocca i comandi. Per collegare il software dell'installatore attraverso la tua rete, attiva prima la [modalità aggiornamento lento](options.md#slow-polling-mode).

### Dopo una modifica alla programmazione della centrale { #after-the-panels-programming-was-changed }

Dopo una modifica alla sua programmazione, fatta con il software dell'installatore o da una tastiera, la centrale può smettere di rispondere per un breve periodo. Nel frattempo Home Assistant mostra le entità come non disponibili, e di nuovo come al solito quando la centrale risponde. Quando la centrale non ha risposto per un po', il log dice per quanto tempo.

L'integrazione non si accorge da sola di una modifica alla programmazione. Per prendere nuovi nomi, zone o scenari, usa **Rileggi la configurazione della centrale** (vedi [Configurazione](setup.md#re-reading-the-panel-configuration)).

### Comandi non eseguiti { #commands-not-carried-out }

*   Il messaggio di errore dice perché (vedi [Quando la centrale rifiuta un comando](#when-the-panel-refuses-a-command)).
*   Controlla che il PIN sia un PIN utente valido della centrale, con le aree che vuoi comandare. La configurazione e **Riconfigura** rifiutano un PIN che non corrisponde a nessun codice utente; per una centrale configurata con una versione precedente dell'integrazione, il log mostra un avviso (*"The PIN does not match any user code of the panel"*) ogni volta che l'integrazione si collega. Cambialo con **Riconfigura** (vedi [Cambiare i dati di connessione](setup.md#changing-the-connection-details-reconfigure)).
*   **L'inserimento viene rifiutato perché ci sono zone non pronte**: chiudi o escludi le zone che il messaggio elenca, oppure imposta **Quando le zone non sono pronte** su *Inserisci comunque e avvisa* (o usa `force: true` in un'azione). Vedi [Zone non pronte](actions-events.md#zones-not-ready).

### Entità mancanti o non aggiornate { #entities-missing-or-not-updating }

*   Controlla i log di Home Assistant (**Impostazioni** → **Sistema** → **Registri**) per gli errori dell'integrazione `inim_smartliving_alarm`.
*   Controlla gli intervalli nelle opzioni di [Aggiornamento](options.md#polling). Troppo brevi possono tenere occupata la scheda; troppo lunghi ritardano gli aggiornamenti. Il log dice quando la centrale non riesce a stare al passo.
*   Ricevono entità solo le voci selezionate: controlla le opzioni in **Aree, zone, scenari, uscite e termostati**.
*   Una zona viene elencata solo se il suo terminale è programmato come zona sulla centrale. Se hai cambiato la programmazione della centrale, usa prima **Rileggi la configurazione della centrale**.

??? note "Casi rari: nessun interruttore, nessun pulsante di scenario"

    Gli interruttori delle aree, delle zone e delle uscite vengono creati solo quando i dati della centrale salvati per le aree hanno i nomi, e i pulsanti degli scenari solo quando gli scenari hanno i nomi. Se mancano tutti, usa **Rileggi la configurazione della centrale**; il log dice quando mancano i nomi.

### Log di debug { #debug-log }

Per ottenere un log di debug, aggiungi questo a `configuration.yaml`, riavvia Home Assistant e ripeti quello che non ha funzionato:

```yaml
logger:
  logs:
    custom_components.inim_smartliving_alarm: debug
```

Il log mostra le richieste e le risposte; il PIN è nascosto, e il nome utente e la password della scheda non vengono mai registrati. Poi togli di nuovo queste righe.
