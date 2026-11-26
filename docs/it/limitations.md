# Limitazioni note { #known-limitations }

Le limitazioni note di questa versione, e cosa puoi fare:

*   L'integrazione interroga la centrale a intervalli (le aree ogni 2 secondi per impostazione predefinita, vedi [Aggiornamento](options.md#polling)), quindi un allarme che inizia e finisce tra due letture non compare mai come *Innescato* sull'entità del pannello di allarme né sul sensore *Area N allarme*. I sensori *memoria allarmi* dell'area e della zona restano accesi dopo e mostrano che è successo, finché non si fa il reset della memoria (vedi [Reset delle memorie allarmi](actions-events.md#resetting-the-alarm-memory)).

*   Mentre Home Assistant legge la centrale agli intervalli di [Aggiornamento](options.md#polling), tiene aperta la connessione alla scheda. La scheda SmartLAN/SI accetta un client locale alla volta, quindi nel frattempo le altre app sulla tua rete (come l'app Inim o il software dell'installatore) non riescono a collegarsi. Attiva la [modalità aggiornamento lento](options.md#slow-polling-mode) per lasciarle collegare tra una lettura di Home Assistant e l'altra, che allora avvengono ogni 30 secondi per impostazione predefinita (**Intervallo di lettura in modalità aggiornamento lento**, in [Aggiornamento](options.md#polling)). L'accesso remoto tramite Inim Cloud non ne risente.

*   Il registro eventi dà un nome a ogni evento che la centrale registra, e a chi e cosa si riferisce. I tasti di emergenza e gli eventi programmabili sono mostrati per numero (*Emergency key 1*, *Programmable event 2*): i loro nomi non vengono letti dalla centrale.

*   Le centrali con firmware 5.0x o di un altro modello configurate con una versione precedente continuano a funzionare con la mappa della 1050 con firmware 6, quindi i loro nomi, zone, scenari e registro eventi possono essere errati e i tempi delle loro aree non vengono mostrati. Un avviso di riparazione lo segnala. Le nuove configurazioni di queste centrali vengono rifiutate.

*   I livelli dei dimmer vengono letti dalla centrale solo con il firmware 6: il firmware 5.1 non li riporta. Con il firmware 5.1 la luminosità di un dimmer è l'ultima impostata da Home Assistant, quindi un livello cambiato da una tastiera non viene mostrato.

*   Termostati delle tastiere: i programmi giornaliero e settimanale si possono cambiare solo dalla tastiera. L'obiettivo mostrato per un programma segue l'orologio di Home Assistant, mentre la centrale segue il suo. Il firmware 5.1 non riporta se un termostato sta riscaldando o raffrescando in quel momento, e i termostati sono stati provati solo su una 1050 con firmware 6.07 tramite SmartLAN/SI, non con il firmware 5.1 né con una scheda SmartLAN/G. Una temperatura ambiente sotto 0 °C viene mostrata come sconosciuta.

*   Le uscite analogiche non vengono proposte. Un'uscita su terminale in una delle prime tre posizioni di terminale della centrale non si può comandare a distanza: il comando delle uscite della centrale non può indirizzare quelle posizioni.

*   Il supporto del firmware 5.1 è sperimentale e limitato: non è stato provato su una centrale reale con firmware 5.1; si presume che gli stati della centrale (come la modalità programmazione) e i risultati dei comandi funzionino come con il firmware 6; gli eventi liberi che ha solo il firmware 5.1 sono mostrati come da *Evento libero 1* a *Evento libero 14*; e il firmware 5.1 non ha sensori video.
