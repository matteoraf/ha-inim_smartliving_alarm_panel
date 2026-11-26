# Inim SmartLiving Alarm per Home Assistant { #inim-smartliving-alarm-for-home-assistant }

Questa integrazione permette a Home Assistant di gestire una centrale di allarme Inim SmartLiving sulla tua rete locale. Puoi vedere lo stato di ogni area e zona, inserire e disinserire, attivare gli scenari della centrale e comandarne le uscite, da plance e automazioni.

![La pagina del dispositivo della centrale in Home Assistant](images/device-page-light.webp#only-light){ width="800" }
![La pagina del dispositivo della centrale in Home Assistant](images/device-page-dark.webp#only-dark){ width="800" }

!!! warning "Non è un prodotto ufficiale Inim"

    Questa integrazione non è realizzata né supportata da Inim Electronics. Usala a tuo rischio.

    Le SmartLiving 1050 e 10100 con firmware 6 sono testate e confermate funzionanti da diversi utenti, sia con la scheda SmartLAN/SI sia con la SmartLAN/G. Le SmartLiving 505 e 515, e tutti e quattro i modelli con firmware 5.1, sono supportati in via sperimentale. Sono ben accetti report e commenti di chi usa queste centrali.

## Per iniziare { #getting-started }

1.  **Verifica i [requisiti](installation.md#requirements)**: una centrale supportata, una scheda SmartLAN/SI o SmartLAN/G sulla tua rete e un codice utente (PIN) della centrale.
2.  **[Installa l'integrazione](installation.md#installing)** e riavvia Home Assistant.
3.  **[Aggiungi la tua centrale](setup.md#adding-a-panel)**: inserisci l'indirizzo della scheda e il PIN, poi scegli cosa importare.
4.  **[Trova le tue entità](entities.md)** nella pagina del dispositivo della centrale e aggiungile a una plancia.

Arrivi da una versione precedente? Leggi prima [Aggiornare la versione](upgrading.md) e fai un backup prima di aggiornare. Qualcosa non funziona? Vedi [Risoluzione dei problemi](troubleshooting.md).

## Cosa ottieni { #what-you-get }

| Parte della centrale | Cosa puoi fare | Pagina |
|---|---|---|
| L'intera centrale | Inserire e disinserire con la scheda *Pannello degli Allarmi*, usando gli scenari della centrale | [Entità](entities.md#the-alarm-panel) |
| Ogni area | Inserirla o disinserirla, sceglierne la modalità di inserimento, vedere allarme, manomissione e memoria allarmi | [Entità](entities.md#areas) |
| Ogni zona | Vedere se è aperta, in manomissione o ha una memoria allarmi; escluderla | [Entità](entities.md#zones) |
| Ogni scenario | Attivarlo con un pulsante; vedere se è attivo | [Entità](entities.md#scenarios) |
| Uscite | Comandare luci, cancelli e altre uscite; regolare i dimmer | [Entità](entities.md#outputs) |
| Termostati delle tastiere | Impostare la temperatura e la modalità del termostato di ogni tastiera; vedere la temperatura ambiente | [Entità](entities.md#thermostats) |
| Registro eventi | Vedere gli eventi recenti della centrale, con i dettagli completi | [Entità](entities.md#sensors) |
| Automazioni | Inserire aree, attivare scenari e fare il reset delle memorie allarmi con le azioni; reagire agli eventi | [Azioni ed eventi](actions-events.md) |

Prima di inserire un'area o attivare uno scenario, l'integrazione chiede alla centrale quali zone non sono pronte, poi rifiuta il comando oppure inserisce comunque con un avviso, come scegli tu (vedi [Zone non pronte](actions-events.md#zones-not-ready)).

## Termini usati in questo sito { #terms }

| Termine | Significato |
|---|---|
| **Centrale** | L'unità di controllo del sistema di allarme SmartLiving. |
| **Scheda SmartLAN** | La scheda di rete nella centrale: **SmartLAN/SI** o **SmartLAN/G**. Home Assistant comunica con la centrale attraverso di essa. |
| **Area** | Una parte della casa che viene inserita e disinserita insieme, per esempio *Piano terra*. Altri sistemi la chiamano partizione. |
| **Zona** | Un ingresso di rilevazione: un contatto su una porta, un sensore di movimento, un rilevatore di fumo. Ogni zona appartiene a una o più aree. |
| **Terminale** | Un collegamento sulla centrale o su un'espansione. Un terminale è programmato come ingresso (una zona), come uscita, o come entrambi. |
| **Terminale doppio e seconda zona** | Un terminale programmato con due zone sugli stessi fili. La sua seconda zona ha un numero oltre quelli dei terminali della centrale: da 51 a 100 sulla SmartLiving 1050, per esempio. |
| **Scenario** | Un preset di inserimento/disinserimento di una o più aree programmato sulla centrale. |
| **Uscita** | Un terminale, o una delle uscite fisse da O1 a O3, che comanda qualcosa: una luce, un cancello, una sirena. |
| **Uscita domotica** | Un'uscita che l'installatore ha contrassegnato come domotica nella programmazione della centrale. Solo queste sono pre-selezionate durante la configurazione. |
| **Memoria allarmi** | Dopo un allarme, la centrale ricorda quali aree e zone lo hanno generato, finché non si fa il reset della memoria. |
| **Esclusione** | Escludere una zona, in modo che non possa generare un allarme finché non viene inclusa di nuovo. |
| **Codice utente e PIN** | Un codice utente è un utente programmato sulla centrale (per esempio *Master*). Il suo PIN è il numero che si digita per usarlo. Home Assistant invia i comandi con un solo PIN, quindi può comandare solo le aree di quel codice utente. |
| **Modalità programmazione** | Mentre qualcuno programma la centrale da una tastiera, la centrale ignora i comandi. Finché la centrale è in modalità programmazione, l'integrazione non invia nessun comando. |

## Contribuire { #contributing }

I contributor sono benvenuti: apri una issue o una pull request su [GitHub](https://github.com/matteoraf/ha-inim_smartliving_alarm_panel).
