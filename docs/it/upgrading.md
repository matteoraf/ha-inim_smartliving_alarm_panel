# Aggiornare la versione { #upgrading }

## Dalla 0.4 { #from-04 }

Questa versione conserva la configurazione della centrale in un nuovo formato. I tuoi ID delle entità e le opzioni restano come sono, e la voce di configurazione viene aggiornata automaticamente. Se arrivi dalla 0.4.0b1 o dalla 0.4.0b2, leggi anche [la sezione qui sotto](#from-040b1-or-040b2).

### Prima di aggiornare { #before-you-update }

1.  **Fai un backup di Home Assistant** (**Impostazioni** → **Sistema** → **Backup**). **Per tornare alla 0.4, ripristina quel backup**: la 0.4 non carica una voce di configurazione nel nuovo formato.

### Dopo l'aggiornamento { #after-you-update }

1.  **Guarda il log** dopo il primo avvio. L'integrazione rilegge da sola la configurazione della centrale una volta, e lo segnala. Se non ci riesce, riprova a ogni avvio (vedi sotto).

### Cosa è cambiato { #what-changed }

| Modifica | Cosa fare |
|---|---|
| Nuovo formato della voce di configurazione; la configurazione della centrale viene riletta una volta | Nulla; per tornare alla 0.4, ripristina il backup |
| Gli interruttori, i sensori delle aree e i selettori della modalità di inserimento non dipendono più dai nomi delle aree salvati | Nulla |
| Gli interruttori di esclusione delle zone trovano il terminale della zona nello stesso modo delle uscite | Nulla |
| Il sensore dello scenario attivo dice *No scenario matches* quando nessuno scenario corrisponde alle aree | *Controlla le tue automazioni* se verificano *Unknown Scenario (Index 255)* o l'indice 255 |
| Il registro eventi non mostra più due volte gli eventi dopo una lettura non riuscita | Nulla |
| I nomi delle entità sono tradotti e non ripetono più il nome della centrale; *Versione firmware* e *Tipo di centrale* sono di diagnostica | Nulla: gli ID delle entità non cambiano; un nome che hai impostato tu viene mantenuto |
| L'unico intervallo di aggiornamento è sostituito da un intervallo per ogni parte dello stato della centrale; la centrale viene letta molto più spesso | Nulla; per leggere meno spesso, vedi le opzioni di *Aggiornamento*; per usare l'app Inim o il software dell'installatore con una scheda SmartLAN/SI, attiva *Modalità aggiornamento lento* |
| I report di sistema leggono i dati di una scheda SmartLAN/SI dalla scheda stessa, anche tra sottoreti diverse; un report *Completo riservato* mostra il codice installatore | Nulla |
| Il registro eventi dà un nome a ogni evento, con nuove chiavi (`event`, `code`, `ended`, `via`, `description_local`, …); `description` usa i nomi degli eventi della centrale, per esempio `Area armed in away mode` invece di `Area(s) Armed`, e `(ended)` invece di `End of …`; i segnaposto `-` e `ff` di `authorized_by`, `device`, `authorized_id` e `device_id` non ci sono più: una chiave assente vuol dire nessuno | *Controlla le tue automazioni* se confrontano una `description` o verificano `-`: confronta invece la nuova chiave `event`, che resta la stessa |
| I nomi dei numeri di telefono della centrale vengono letti con la configurazione della centrale, per il registro eventi; la configurazione viene riletta una volta al primo avvio | Nulla |
| L'elenco `event_log` non viene più conservato nello storico di Home Assistant (la centrale conserva il suo registro, e l'elenco viene riletto dopo un riavvio); gli altri attributi del sensore del registro eventi sì | Nulla |
| Termostati delle tastiere: un'entità clima e un sensore di temperatura per ogni tastiera che riporta una temperatura ambiente; le tastiere vengono controllate una volta, in background dopo il primo avvio, e i loro termostati aggiunti | *Deseleziona* i termostati che non usi, in **Aree, zone, scenari, uscite e termostati** nelle opzioni |

??? note "Dettagli"

    *   **La voce di configurazione viene aggiornata a un nuovo formato (versione 2.2).** Ora conserva solo i dati della centrale come letti, e nomi, zone e scenari vengono ricavati da questi. Al primo avvio l'integrazione rilegge da sola la configurazione della centrale una volta (il log lo segnala). Se non ci riesce, riprova a ogni avvio; fino ad allora i nomi di codici utente, chiavi, lettori, espansioni, timer, sirene e numeri di telefono mostrano i loro valori predefiniti (come *Code 1*, *Reader 1* o *Key 5*), e su una voce configurata per la prima volta prima della 0.4.0b1 gli interruttori di esclusione delle zone compaiono solo quando quella lettura è riuscita. **Rileggi la configurazione della centrale** la legge subito. Gli ID delle entità e le opzioni non cambiano. **Per tornare alla 0.4, ripristina il backup fatto prima dell'aggiornamento**: la 0.4 non carica una voce nel nuovo formato.
    *   **Gli interruttori, i sensori delle aree e i selettori della modalità di inserimento non dipendono più dai nomi delle aree salvati.** Prima, una configurazione della centrale salvata senza di essi (per esempio dopo una lettura incompleta) lasciava la centrale senza nessun interruttore, compresi quelli di esclusione delle zone e delle uscite, e senza i sensori di allarme, manomissione e memoria allarmi delle aree e i selettori della modalità di inserimento. Ora ognuno viene creato per conto suo, un'area senza nome si chiama *Area N*, e i pulsanti degli scenari mostrano cosa fanno a ogni area.
    *   **Gli interruttori di esclusione delle zone trovano il terminale della zona nell'elenco dei terminali della centrale nello stesso modo delle uscite.** Una zona nell'ultima posizione dell'elenco (un terminale di tastiera su una centrale usata del tutto) ora ha un interruttore di esclusione; prima non ne aveva. Se un terminale compare due volte nell'elenco, l'interruttore usa la sua prima posizione, e l'attributo `internal_index` della zona la mostra.
    *   **Quando nessuno scenario corrisponde allo stato delle aree** (nessuno scenario corrisponde, o la centrale non ne riporta), il sensore *Scenario attivo* ora dice *No scenario matches* e il suo attributo `active_scenario_index` è vuoto; prima diceva *Unknown Scenario (Index 255)* e 255. `raw_active_scenario_hex` dà ancora il valore riportato dalla centrale. Il pannello di allarme mostra ancora *Attivo con esclusione personalizzata* in quel caso, come prima.
    *   **Il registro eventi non mostra più due volte gli eventi.** Quando una lettura non riusciva dopo una riuscita, gli eventi della lettura riuscita venivano aggiunti al registro una seconda volta.
    *   **I nomi delle entità sono tradotti e si leggono allo stesso modo ovunque**: *Zona 12 (Finestra)*, *Zona 12 manomissione (Finestra)*, *Area 3 allarme (Cucina)*, *Attiva scenario 2 (Notte)*, *Registro eventi*, mostrati dopo il nome della centrale e nella lingua di Home Assistant. L'interruttore della zona ora è *Zona N inclusa*. Una voce senza nome sulla centrale mostra solo il suo numero (*Zona 5*, non *Zona 5 (Zona 5)*). Gli ID delle entità non cambiano, quindi automazioni e plance continuano a funzionare; le schede che mostrano il nome di un'entità mostrano quello nuovo, e un nome che hai impostato tu viene mantenuto.
    *   ***Versione firmware* e *Tipo di centrale*** passano nella sezione *Diagnostica* della pagina del dispositivo.
    *   **Le nuove installazioni non hanno più il nome della centrale due volte negli ID delle entità** (per esempio `switch.home_area_3` invece di `switch.home_home_area_3`). Gli ID delle entità di un'installazione esistente restano come sono; per togliere un nome doppio, rinomina l'ID entità nelle impostazioni dell'entità (**Impostazioni** → **Dispositivi e servizi** → **Entità**, l'entità, poi l'icona dell'ingranaggio), e aggiorna le automazioni e le plance che la usano.
    *   **La centrale viene letta a parti, ciascuna con il proprio intervallo** ([Aggiornamento](options.md#polling)): zone e uscite ogni secondo, aree e scenario attivo ogni 2 secondi, il registro eventi ogni 10 secondi, l'esclusione delle zone e i livelli dei dimmer ogni 30 secondi, lo stato della centrale ogni 60 secondi, e ogni parte subito dopo un comando inviato da Home Assistant. La vecchia opzione **Intervallo di aggiornamento (secondi)** viene eliminata e valgono i nuovi valori predefiniti, qualunque fosse la sua impostazione; imposta gli intervalli in **Impostazioni** → **Dispositivi e servizi** → **Inim SmartLiving Alarm**, l'icona dell'ingranaggio nella riga della tua centrale, **Aggiornamento**. Mentre legge la centrale a questi intervalli, Home Assistant tiene aperta la connessione alla scheda; la scheda SmartLAN/SI accetta un client locale alla volta, quindi nel frattempo le altre app sulla tua rete non riescono a collegarsi. Per lasciar collegare l'app Inim o il software dell'installatore, attiva il nuovo interruttore **Modalità aggiornamento lento** nella pagina del dispositivo: Home Assistant legge allora tutta la centrale ogni 30 secondi per impostazione predefinita (**Intervallo di lettura in modalità aggiornamento lento**, in **Aggiornamento**) e chiude la connessione dopo ogni lettura, come facevano le versioni precedenti, e l'interruttore si spegne da solo dopo 60 minuti (vedi [Modalità aggiornamento lento](options.md#slow-polling-mode)).
    *   **I report di sistema leggono i dati della scheda SmartLAN/SI dalla scheda stessa** (le sue impostazioni di rete e il firmware), quindi sono inclusi anche quando Home Assistant è in un'altra sottorete. Un report **Completo riservato** mostra anche il codice installatore della centrale (vedi [Trovare il codice installatore](system-report.md#finding-the-installer-code)), e il nome utente del DNS dinamico della scheda ora compare solo con *Completo riservato*, come le sue password.
    *   **I termostati delle tastiere sono nuovi** ([Termostati](entities.md#thermostats)): ogni tastiera che riporta una temperatura ambiente riceve un'entità clima (spento, riscaldamento o freddo; le modalità manuale, programma giornaliero, programma settimanale e antigelo della centrale come preimpostazioni) e un sensore di temperatura. Al primo avvio, quando l'integrazione è in funzione, controlla quali tastiere riportano una temperatura, ne seleziona i termostati nelle opzioni e ne aggiunge le entità. Anche una tastiera di cui non usi il termostato ne riceve uno: deselezionalo nelle opzioni per rimuoverne le entità. Le modifiche da Home Assistant impostano solo la modalità, la stagione e il valore manuale; i programmi non vengono cambiati.
    *   **Il registro eventi dà un nome a ogni evento** ([Sensori](entities.md#sensors)). Prima, solo i più comuni avevano una descrizione e gli altri comparivano come *Unknown Action*. `description` ora usa i nomi che la centrale dà ai suoi eventi, in inglese: per esempio *Area armed in away mode* (era *Area(s) Armed*), *Area disarmed* (*Area(s) Disarmed*), *Zone bypassed (ended)* (*End of Zone Excluded*), *Scenario activated* (*Scenario Activated*). Nuove chiavi indicano la chiave dell'evento (`event`, uguale in ogni lingua), il suo codice, se è terminato, come è avvenuto (`via`), e di cosa si tratta (una tastiera, un timer, un'espansione…); `description_local` dà la descrizione nella lingua di Home Assistant. Un'area inserita da una zona di comando o da un timer ora lo dice, dove prima il numero della zona compariva come codice utente o il timer come lettore. Quando un evento non ha codice utente, chiave, tastiera o lettore, `authorized_by`, `device`, `authorized_id` e `device_id` non sono presenti: prima valevano `-` o `ff`. L'elenco `event_log` non viene più conservato nello storico di Home Assistant.

## Dalla 0.4.0b1 o dalla 0.4.0b2 { #from-040b1-or-040b2 }

*   **Controlla le tue automazioni:** nell'evento `inim_smartliving_alarm_zones_not_ready`, `zone_name` ora è il nome semplice della zona (per esempio `Finestra cucina`), come nelle entità delle zone e nell'evento di scenario non riuscito. Le automazioni che confrontano un nome con il numero tra parentesi, come `Finestra cucina (12)`, vanno aggiornate; `zone_id` dà ancora il numero.

## Dalla 0.3.x { #from-03x }

La versione 0.4 rinnova il modo in cui l'integrazione parla con la centrale. I tuoi ID delle entità e le opzioni restano come sono, e la voce di configurazione viene aggiornata automaticamente.

### Prima di aggiornare { #before-you-update_1 }

1.  **Fai un backup di Home Assistant** (**Impostazioni** → **Sistema** → **Backup**), soprattutto per un'anteprima o una beta (vedi [Versioni di anteprima](installation.md#preview-versions)).
2.  **Leggi le modifiche qui sotto**, soprattutto quelle segnate *controlla le tue automazioni*.

### Dopo l'aggiornamento { #after-you-update_1 }

1.  **Guarda il log** dopo il primo avvio. L'integrazione può rileggere da sola la configurazione della centrale, una volta, e lo segnala.
2.  **Elimina le entità rimaste**: le entità di terminali che non sono zone, o di zone e aree che la tua centrale non ha, restano come entità non disponibili e sono elencate nel log a ogni avvio. Eliminale in **Impostazioni** → **Dispositivi e servizi** → **Entità**.
3.  **Seleziona le novità** nelle opzioni, in **Aree, zone, scenari, uscite e termostati**: le seconde zone dei terminali doppi, le aree da 11 a 15 e le zone oltre la 50 su una 10100, e le uscite.
4.  **Scegli cosa succede con le zone non pronte**: ora l'inserimento controlla le zone, e per impostazione predefinita rifiuta (vedi [Zone non pronte](actions-events.md#zones-not-ready)).
5.  **Controlla le tue automazioni e i tuoi modelli** rispetto alle modifiche segnate *controlla le tue automazioni*.

### Cosa è cambiato { #what-changed_1 }

#### Aree e pannello di allarme { #areas-and-the-alarm-panel }

| Modifica | Cosa fare |
|---|---|
| Gli interruttori delle aree sono accesi anche con l'inserimento parziale e immediato | *Controlla le tue automazioni* se contavano sull'interruttore spento in quei casi |
| *Area X Triggered* ora è *Area X memoria allarmi*; nuovi sensori *Area X allarme* e *Area X manomissione* | Nulla: stesso ID entità |
| Il pannello di allarme mostra *Innescato* solo mentre un'area è in allarme | *Controlla le tue automazioni*: usa i sensori *memoria allarmi* per la memoria |
| Nuovo selettore della modalità di inserimento per ogni area, e l'azione `arm_area` | Aggiungili a plance e automazioni se vuoi |
| L'interruttore di ogni area può inserire nella sua modalità | Facoltativo: **Modalità di inserimento per area** |
| I tempi di ogni area sono mostrati come attributi | Nulla |

??? note "Dettagli"

    *   **Gli interruttori delle aree** ora sono accesi anche per un'area inserita in modalità parziale o immediata (prima, un'area così compariva come sconosciuta e il suo interruttore era spento). Accendere un interruttore inserisce ancora in modalità totale, a meno che tu non scelga un'altra modalità con la nuova opzione **Modalità di inserimento predefinita delle aree**. Il nuovo attributo `arming_mode` indica la modalità (`total`, `partial`, `instant` o `disarmed`); `status_text` resta `armed` o `disarmed`.
    *   **"Area X Triggered" ora si chiama "Area X memoria allarmi"**: stesso ID entità, stesso significato (acceso dopo un allarme finché non si fa il reset della memoria), ora anche per le aree 9 e 10, con un attributo `tamper_memory`. Due nuovi sensori binari per area mostrano lo stato **attuale**: *Area X allarme* e *Area X manomissione*.
    *   **Il pannello di allarme mostra Innescato solo mentre un'area è in allarme.** Prima restava Innescato finché non si faceva il reset della memoria allarmi; ora torna al suo stato inserito o disinserito quando l'allarme finisce, e i sensori *memoria allarmi* conservano la memoria. Le automazioni che contavano su Innescato fino al reset devono usare invece i sensori *memoria allarmi*.
    *   **Novità: un selettore della modalità di inserimento per ogni area** (*Area X modalità di inserimento*: disinserita, totale, parziale o immediata) e l'**azione `arm_area`**, che inserisce o disinserisce più aree con un solo comando (vedi [Inserire aree dalle automazioni](actions-events.md#arming-areas-from-automations)).
    *   **L'interruttore di ogni area può inserire nella sua modalità**: il nuovo passaggio delle opzioni **Modalità di inserimento per area** ha una scelta per ogni area importata (usa la predefinita, totale, parziale o immediata). Le aree lasciate su *Usa la predefinita* seguono **Modalità di inserimento predefinita delle aree**, come prima. L'opzione generale, chiamata **Area switches arm in** nell'anteprima 0.4.0a3, ora è **Modalità di inserimento predefinita delle aree**: se l'avevi cambiata in quell'anteprima, impostala di nuovo (è tornata a totale).
    *   **I tempi di ogni area sono mostrati**: gli attributi del selettore *Area X modalità di inserimento* ora comprendono i tempi di uscita e di ingresso dell'area, il secondo tempo di ingresso, i tempi di preavviso di inserimento e di ronda, il programma orario e tre opzioni, letti dalla centrale. Al primo avvio l'integrazione rilegge da sola la configurazione della centrale una volta per ottenerli (il log lo segnala); se non ci riesce, compaiono dopo l'avvio successivo o dopo **Rileggi la configurazione della centrale**.

#### Zone { #zones }

| Modifica | Cosa fare |
|---|---|
| Le zone seguono i tipi di terminale della centrale; si possono aggiungere le seconde zone dei terminali doppi | Elimina le entità rimaste; seleziona le seconde zone se le vuoi |
| *Zone X Triggered* ora è *Zona X memoria allarmi*; nuovo sensore *Zona X manomissione* | Nulla: stesso ID entità |
| Le zone vengono controllate anche prima di inserire le aree, e per impostazione predefinita l'inserimento viene rifiutato | Scegli **Quando le zone non sono pronte** |
| Gli attributi delle zone mostrano la programmazione reale; alcuni sono rinominati | *Controlla le tue automazioni* e i tuoi modelli |
| Le classi del dispositivo delle zone seguono la programmazione della zona | Imposta **Mostra come** su una zona per mantenerne la vecchia classe |

??? note "Dettagli"

    *   **Le zone seguono i tipi di terminale della centrale.** I terminali di uscita (per esempio una sirena) e i terminali inutilizzati non ricevono più entità di zona, e si possono aggiungere le seconde zone dei terminali doppi (da 11 a 20 sulla 505, da 21 a 40 sulla 515, da 51 a 100 sulla 1050, da 101 a 200 sulla 10100): selezionale nelle opzioni in **Aree, zone, scenari, uscite e termostati** (le installazioni esistenti mantengono le zone che avevano). Le entità che le versioni precedenti avevano creato per terminali che non sono zone **non vengono eliminate automaticamente**: restano come entità non disponibili, e il log le elenca a ogni avvio (*"these entities belong to terminals that are not zones on the panel"*). Eliminale in **Impostazioni** → **Dispositivi e servizi** → **Entità**.
    *   **"Zone X Triggered" ora si chiama "Zona X memoria allarmi"** (stesso ID entità). Le zone in cortocircuito o in manomissione non compaiono più come sconosciute: si accende un nuovo sensore *Zona X manomissione*, e lo `status_text` della zona dice `short` o `tamper`. Queste zone vengono elencate anche quando impediscono l'attivazione di uno scenario.
    *   **Le zone vengono controllate anche prima di inserire le aree.** Gli interruttori delle aree, i selettori della modalità di inserimento e l'azione `arm_area` ora chiedono alla centrale quali zone non sono pronte, come già facevano gli scenari, e per impostazione predefinita **rifiutano l'inserimento** quando ce ne sono (prima inserivano con le zone aperte). La nuova opzione **Quando le zone non sono pronte** (Impostazioni generali) può invece inserire comunque con un avviso; `force: true` lo fa per una singola chiamata dell'azione. Una nuova azione, `activate_scenario`, attiva uno scenario dalle automazioni, e un nuovo evento, `inim_smartliving_alarm_zones_not_ready`, elenca ogni volta le zone. L'integrazione legge anche se la centrale stessa rifiuta l'inserimento con zone non pronte (nuovo sensore di diagnostica *Rifiuta l'inserimento con zone non pronte*): al primo avvio rilegge da sola la configurazione della centrale una volta (il log lo segnala). I sensori delle zone mostrano come attributi le opzioni di programmazione di ogni zona (`autobypass`, `no_arming_if_not_ready`, `interior` e altre).
    *   **Gli attributi delle zone ora mostrano la programmazione reale della zona**, letta dalla configurazione della centrale già salvata (non viene riletto nulla). Prima, la maggior parte dei tipi di zona e dei bilanciamenti compariva come *Unknown*, e due tempi venivano letti in modo errato. Alcuni attributi sono rinominati, quindi controlla i modelli e le automazioni che li usano: `activation_type` ora è `zone_type`, `balancing_type` è `balancing`, `sensor_type` è `sensor_kind`, `duration_ms` ora è `alarm_pulse_duration` (o `shock_sensitivity` per i sensori inerziali), `time` è `pulse_window_time` e `pulses` è `pulse_count`. Nuovo: `alarm_cycles`. I valori ora sono chiavi inglesi in minuscolo (per esempio `delayed`, `double_eol`, `input` invece di *Delayed*, *Double Balancing*, *Input*).
    *   **Le classi del dispositivo delle zone seguono la programmazione della zona.** Le zone a bilanciamento singolo ora sono *apertura* (prima, nessuna classe), i sensori inerziali sono *vibrazione* (prima, *apertura* o nessuna), e i sensori tapparella sono *apertura* (prima, *movimento*). Le zone normalmente aperte, normalmente chiuse e a doppio bilanciamento mantengono la loro classe. Per mantenere la vecchia classe su una zona, impostala con **Mostra come** nelle impostazioni dell'entità.

#### Scenari { #scenarios }

| Modifica | Cosa fare |
|---|---|
| Le nuove configurazioni propongono gli scenari di inserimento totale (fuori) e di disinserimento | Nulla: le installazioni esistenti mantengono le loro associazioni |
| Gli scenari che inseriscono in modalità parziale o immediata vengono letti correttamente | *Controlla le tue automazioni* che usano `area_actions_details` |
| Uno scenario bloccato da zone non pronte elenca le zone riportate dalla centrale | Nulla |

??? note "Dettagli"

    *   **Le nuove configurazioni propongono gli scenari di inserimento totale (fuori) e di disinserimento** in base a cosa fanno i tuoi scenari alle aree che importi. Le installazioni esistenti mantengono le loro associazioni.
    *   **Gli scenari che inseriscono in modalità parziale o immediata vengono letti correttamente.** L'attributo `area_actions_details` dei pulsanti degli scenari ora dice `total`, `partial` o `instant` per le aree che uno scenario inserisce (prima, `arm` per totale e `unknown` per le altre due). Uno scenario che disinserisce alcune aree e ne inserisce altre in modalità parziale o immediata veniva trattato come scenario di disinserimento, quindi se la centrale lo rifiutava, il messaggio non elencava le zone che l'avevano bloccato; ora lo fa.
    *   **Uno scenario bloccato da zone non pronte elenca le zone riportate dalla centrale.** La notifica persistente e i `blocking_zones` dell'evento `inim_smartliving_alarm_scenario_activation_failed` ora elencano le stesse zone del messaggio di errore, comprese le zone a riposo che la centrale considera non pronte (per esempio le zone ritardate), ognuna con `status` `not_ready`. Prima elencavano solo le zone in allarme o in manomissione, e potevano dire che non era stata trovata nessuna zona.

#### Comandi ed errori { #commands-and-errors }

| Modifica | Cosa fare |
|---|---|
| I comandi che non vengono eseguiti ora mostrano un errore | *Controlla le tue automazioni*: un passaggio non riuscito le ferma, a meno che non abbia `continue_on_error: true` |
| La modalità programmazione viene rilevata; in quel momento non viene inviato nessun comando | Nulla |

??? note "Dettagli"

    *   **I comandi che non vengono eseguiti ora mostrano un errore.** Prima, un inserimento, un disinserimento, un'esclusione o un reset rifiutati venivano solo scritti nel log, e le notifiche di errore del pannello di allarme non comparivano affatto. Ora l'azione non riesce, con un messaggio che dice perché (per esempio un PIN che la centrale non accetta più, o un PIN non abilitato per quell'area), nella plancia e nelle tracce delle automazioni; vedi [Quando la centrale rifiuta un comando](troubleshooting.md#when-the-panel-refuses-a-command). Un passaggio di un'automazione che non riesce ferma quell'automazione, a meno che il passaggio non abbia `continue_on_error: true`. L'evento `inim_smartliving_alarm_scenario_activation_failed` ha un nuovo campo `panel_result` (la risposta della centrale, oppure `null` quando la centrale non ha rifiutato il comando), e il suo `reason` ora può essere anche `command_failed` o `invalid_code`.
    *   **La modalità programmazione ora viene rilevata.** Mentre la centrale viene programmata da una tastiera, ignora i comandi, e le versioni precedenti riportavano un comando del genere come eseguito. Ora non viene inviato nessun comando mentre la centrale è in programmazione: l'azione invece non riesce, con un messaggio, e la configurazione, **Riconfigura** e **Rileggi la configurazione della centrale** vengono rifiutati con un messaggio: riprova al termine della programmazione. Un nuovo sensore di diagnostica, *Modalità programmazione*, lo mostra. Il `reason` dell'evento `inim_smartliving_alarm_scenario_activation_failed` ora può essere anche `panel_programming`.

#### Registro eventi e nomi { #event-log-and-names }

| Modifica | Cosa fare |
|---|---|
| Gli orari degli eventi sono nel fuso orario di Home Assistant; lo stato è la posizione dell'evento nel registro | *Controlla le tue automazioni* che usano il valore dello stato |
| Il registro eventi mostra i nomi programmati sulla centrale; l'opzione **Nomi dei Lettori** viene eliminata | Nulla: i nomi arrivano dalla centrale |
| I nomi con lettere accentate le mostrano per intero | Nulla |

??? note "Dettagli"

    *   **Registro eventi:** gli orari degli eventi ora sono mostrati nel fuso orario di Home Assistant, con l'ora legale (prima, sempre UTC+1), e i nuovi eventi continuano a comparire dopo che il registro eventi della centrale si è riempito ed è ripartito dall'inizio. Lo stato del sensore del registro eventi ora è la posizione dell'evento più recente nel registro della centrale (da 0 a 999, oppure da 0 a 499 sulle centrali che conservano 500 eventi: le SmartLiving 505 e 515, e la 1050 con firmware 5.1) invece di un indirizzo di memoria: le automazioni che si attivano solo a un cambiamento continuano a funzionare, ma confronta i nuovi numeri se usavi il valore stesso.
    *   **Il registro eventi mostra i nomi programmati sulla centrale**: chi ha causato un evento (il nome del codice utente o della chiave, invece di *User 3* o *Key 5*) e dove (il nome della tastiera o del lettore). I comandi inviati a distanza, dall'app, dal cloud, da Home Assistant o dal software dell'installatore, mostrano *Remote* (prima, *Mobile App*). L'opzione **Nomi dei Lettori** viene eliminata: i nomi dei lettori ora arrivano dalla centrale, e i nomi scritti in quell'opzione non vengono più usati. La voce di configurazione viene aggiornata ancora una volta (versione 1.5), e al primo avvio l'integrazione rilegge da sola la configurazione della centrale per ottenere i nomi (il log lo segnala). Un nuovo sensore di diagnostica, *Periferiche*, elenca le tastiere, i lettori, le espansioni e le sirene abilitati sulla centrale, e lo fa anche il passaggio **Aree, zone, scenari, uscite e termostati**.
    *   **I nomi con lettere accentate ora le mostrano per intero.** I nomi di aree, zone, scenari e tastiere programmati con lettere come à, è, é o ù prima le perdevano; ora vengono letti con il set di caratteri della centrale, come i nomi dei codici utente, delle chiavi e degli altri dispositivi. I nomi arrivano dai dati della centrale già salvati, quindi non viene riletto nulla. Gli ID delle entità non cambiano, e i nomi che hai cambiato in Home Assistant restano come li hai impostati.

#### Configurazione, opzioni e connessione { #setup-options-and-connection }

| Modifica | Cosa fare |
|---|---|
| Una sola connessione alla centrale, riutilizzata | Nulla; vedi [l'app Inim](troubleshooting.md#the-inim-app-cannot-connect-locally) |
| Novità: schede SmartLAN/G, una ricerca nella rete, tutte le aree del tuo codice utente nello scenario attivo | Nulla |
| Il PIN viene verificato quando usi **Riconfigura**; cambiarlo nelle opzioni funziona di nuovo | Nulla |
| Scegli aree, zone e scenari da elenchi; deselezionare rimuove le entità | Nulla: i tuoi conteggi sono diventati le stesse voci |
| Le risposte delle schede SmartLAN/G vengono lette correttamente | Nulla |

??? note "Dettagli"

    *   La voce di configurazione viene aggiornata automaticamente (viene contrassegnata come connessione SmartLAN/SI). Gli ID delle entità e le opzioni non cambiano.
    *   Home Assistant ora tiene una sola connessione alla centrale e la riutilizza, quindi gli aggiornamenti sono più rapidi. La scheda SmartLAN/SI accetta una sola connessione locale: vedi [l'app Inim](troubleshooting.md#the-inim-app-cannot-connect-locally) in Risoluzione dei problemi.
    *   D'ora in poi il PIN viene verificato quando usi **Riconfigura**: un PIN che non corrisponde a nessun codice utente della centrale viene rifiutato. Se il tuo PIN attuale non corrisponde a nessuno, il log mostra un avviso a ogni connessione.
    *   Novità: le schede SmartLAN/G, una ricerca nella rete quando aggiungi una centrale, e lo scenario attivo ora tiene conto di tutte le aree del tuo codice utente (prima le aree da 8 a 10 venivano ignorate).
    *   La voce di configurazione viene aggiornata ancora una volta (versione 1.3): l'elenco delle zone viene completato dai dati della centrale già salvati, senza contattare la centrale. Se quei dati sono incompleti, un avviso ti chiede di usare **Rileggi la configurazione della centrale**.
    *   **SmartLAN/G:** le risposte della scheda ora vengono lette come la scheda le invia (solo i dati, senza il checksum della centrale). Prima, ogni lettura non riusciva e la configurazione segnalava un nome utente o una password errati anche con le credenziali giuste. Un nome utente o una password errati ora vengono segnalati solo quando la risposta della scheda non si può decifrare; ogni altra risposta inattesa ha il suo messaggio (vedi [Risoluzione dei problemi](troubleshooting.md#smartlang-the-reply-was-not-in-the-expected-format)).
    *   **Ora scegli le aree, le zone e gli scenari da importare da elenchi**, per nome, invece di indicare quanti ne vuoi di ciascuno. La voce di configurazione viene aggiornata ancora una volta (versione 1.4): i conteggi che avevi diventano le stesse voci (per esempio, 7 aree diventano le aree da 1 a 7), quindi le tue entità non cambiano. Uno scenario usato da una modalità del pannello di allarme oltre il tuo numero di scenari viene aggiunto, così la modalità continua a funzionare. Ora le opzioni aprono un menu: **Aree, zone, scenari, uscite e termostati** o **Impostazioni generali**. Deselezionare lì una voce **rimuove le sue entità**; selezionandola di nuovo, tornano con gli stessi ID delle entità. Le entità rimaste di aree, zone o scenari oltre i tuoi vecchi conteggi vengono rimosse al primo avvio.
    *   **L'azione `arm_area` elenca solo le aree importate, per nome**, e rifiuta le aree che non sono importate. Le modalità del pannello di allarme si possono associare solo a scenari importati.
    *   **Cambiare il PIN nelle opzioni funziona di nuovo**, e il PIN non viene più mostrato. Il campo **Nuovo codice PIN** in **Impostazioni generali** ora è vuoto: lascialo vuoto per mantenere il PIN attuale, oppure inseriscine uno nuovo, che viene verificato con la centrale come durante la configurazione. Prima il campo mostrava il PIN attuale in chiaro e ogni nuovo PIN veniva rifiutato. Il PIN (e, per SmartLAN/G, la password della scheda) ora è un campo password durante la configurazione e in **Riconfigura**, dove non viene più precompilato: lascialo vuoto per mantenere quello attuale. Un nuovo sensore di diagnostica, *Codice utente*, indica il codice utente a cui appartiene il PIN, e lo dice anche **Impostazioni generali**.
    *   **Una parte maggiore dell'integrazione è tradotta.** Le notifiche (uno scenario bloccato o non riuscito, un'area inserita con zone non pronte), le scelte degli scenari per le modalità del pannello di allarme nelle opzioni, e i nomi e i campi delle azioni ora seguono la lingua di Home Assistant (inglese o italiano). Il `message` dell'evento `inim_smartliving_alarm_scenario_activation_failed` resta in inglese. L'azione `alarm_reset` su una centrale la cui integrazione non è caricata ora non riesce, con un messaggio, invece di non fare nulla.

#### Centrali e firmware { #panels-and-firmware }

| Modifica | Cosa fare |
|---|---|
| Le SmartLiving 10100, 505 e 515 usano la propria mappa di memoria | Guarda il log; elimina le entità rimaste; seleziona le nuove aree e zone |
| Le centrali con firmware 5.1 usano la propria mappa (sperimentale) | Guarda il log |
| Il firmware 5.0x e gli altri modelli non si possono più configurare | Le configurazioni esistenti continuano a funzionare, con un avviso di riparazione |

??? note "Dettagli"

    *   **SmartLiving 10100 (sperimentale con firmware 5.1):** una 10100 ora usa la propria mappa di memoria. Le versioni precedenti la leggevano come se fosse una 1050, quindi i suoi nomi e le sue zone erano errati: al primo avvio dopo l'aggiornamento l'integrazione rilegge da sola la configurazione della centrale (il log lo segnala). Gli ID delle entità delle aree e delle zone restano gli stessi; le aree da 11 a 15 e le zone oltre la 50 si possono selezionare nelle opzioni in **Aree, zone, scenari, uscite e termostati**.
    *   **SmartLiving 505 e 515 (sperimentali):** ora usano la propria mappa di memoria. Le versioni precedenti le leggevano come se fossero una 1050, quindi i loro nomi, zone, scenari e registro eventi erano errati. Al primo avvio dopo l'aggiornamento l'integrazione rilegge da sola la configurazione della centrale (il log lo segnala). Gli ID delle entità restano gli stessi; le zone e le aree che la centrale non ha restano come entità non disponibili e sono elencate nel log, come per la 10100: eliminale in **Impostazioni** → **Dispositivi e servizi** → **Entità**. Segnala come funziona.
    *   **Firmware 5.1 (sperimentale):** le centrali con firmware 5.1 ora usano la propria mappa di memoria. Le versioni precedenti le leggevano come se fossero una 1050 con firmware 6, quindi i loro nomi, zone, scenari e registro eventi erano errati. Al primo avvio dopo l'aggiornamento l'integrazione rilegge da sola la configurazione della centrale (il log lo segnala), e l'avviso di riparazione "centrale non supportata" scompare. Le descrizioni del registro eventi ora sono corrette su queste centrali. Vedi i [limiti del supporto del firmware 5.1](limitations.md). Segnala come funziona.
    *   **Il firmware 5.0x e gli altri modelli non si possono più configurare**: una nuova configurazione di una centrale con firmware precedente al 5.1, o di un modello SmartLiving senza la propria mappa, viene rifiutata con un messaggio. Una centrale di un modello del genere, o con firmware 5.0x, già configurata continua a funzionare come prima e mostra un avviso di riparazione, perché i suoi dati possono essere errati.

#### Novità { #new-features }

| Modifica | Cosa fare |
|---|---|
| Le uscite si possono comandare e regolare | Selezionale nelle opzioni |
| Report di sistema | Vedi [Report di sistema](system-report.md) |
| L'integrazione ha la sua icona | Mostrata con Home Assistant 2026.3 o successivo |

??? note "Dettagli"

    *   **Le uscite sono nuove**: le uscite su terminale e le uscite fisse da O1 a O3 si possono comandare, e i dimmer regolare, da Home Assistant. Al primo avvio l'integrazione rilegge da sola la configurazione della centrale una volta per ottenere la programmazione e i nomi delle uscite (il log lo segnala). Nessuna uscita viene importata finché non salvi le opzioni in **Aree, zone, scenari, uscite e termostati**, dove le uscite che l'installatore ha contrassegnato come domotiche sono già selezionate per te.
    *   **I report di sistema sono nuovi**: scaricare la diagnostica dell'integrazione ora dà un report di sistema per le segnalazioni di problemi, al livello scelto con il nuovo selettore *Livello diagnostica* (disattivato per impostazione predefinita). Vedi [Condividere un report di sistema](system-report.md).
    *   **L'integrazione ha la sua icona**, mostrata con Home Assistant 2026.3 o successivo; le versioni precedenti non ne mostrano nessuna, come prima.

Limiti di questa versione: vedi [Limitazioni note](limitations.md).
