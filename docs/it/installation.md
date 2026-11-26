# Installazione { #installation }

Questa pagina spiega cosa serve e come installare l'integrazione in Home Assistant.

## Requisiti { #requirements }

Ti servono:

*   **Home Assistant 2026.1 o successivo.**
*   **Una centrale Inim SmartLiving supportata**, con firmware 6.x o 5.1x (vedi la tabella qui sotto).
*   **Una scheda SmartLAN/SI o SmartLAN/G** nella centrale, collegata alla tua rete e raggiungibile da Home Assistant.
*   **L'indirizzo e la porta della scheda** (di solito 5004). Per una scheda SmartLAN/G, anche il nome utente e la password della pagina web della scheda.
*   **Un codice utente (PIN) della centrale.** I comandi vengono inviati con questo codice, quindi deve comandare le aree che vuoi inserire e disinserire.

### Centrali supportate { #supported-panels }

| Modello | Aree | Numeri di zona | Stato |
|---|---|---|---|
| SmartLiving 1050 | 10 | 1-100 | sviluppata su questa centrale; provata e funzionante secondo più utenti (SmartLAN/SI e SmartLAN/G) |
| SmartLiving 10100 | 15 | 1-200 | provata e confermata funzionante da più utenti (SmartLAN/SI e SmartLAN/G) |
| SmartLiving 515 | 5 | 1-40 | **sperimentale** |
| SmartLiving 505 | 5 | 1-20 | **sperimentale** |

I numeri di zona oltre il numero dei terminali sono le seconde zone dei terminali doppi.

??? note "Cosa vuol dire sperimentale, e le altre versioni del firmware"

    *   **I modelli sperimentali** usano la propria mappa di memoria, ma non sono ancora stati provati su una centrale reale; un avviso nel log lo segnala. Se usi una di queste centrali, il tuo feedback è utile a migliorare l'integrazione.
    *   **Il firmware 6.x** è supportato come nella tabella qui sopra.
    *   **Il firmware 5.1x è supportato in via sperimentale, e con dei limiti**, su tutti e quattro i modelli:

        *   non è stato provato su una centrale reale con firmware 5.1;
        *   si presume che gli stati della centrale (come la modalità programmazione) e i risultati dei comandi funzionino come con il firmware 6;
        *   il registro eventi mostra gli eventi liberi che ha solo il firmware 5.1 come da *Evento libero 1* a *Evento libero 14*;
        *   i sensori video non sono supportati (il firmware 5.1 non ne ha).

        Un avviso nel log segnala che il supporto della centrale è sperimentale. Segnala come funziona per te.
    *   **Centrali con firmware 5.0x e precedenti** non si possono configurare ("Il firmware della centrale non è supportato: serve il firmware 5.1 o successivo").
    *   **Eventuali altri modelli SmartLiving** non si possono configurare ("Questo modello SmartLiving non è ancora supportato"); segnala il tuo modello e il firmware su GitHub.

??? note "SmartLAN/SI e SmartLAN/G"

    Entrambe le schede sono supportate, ed entrambe sono confermate come funzionanti sulle SmartLiving 1050 e 10100.

## Installazione dell'integrazione { #installing }

L'integrazione si installa come repository personalizzato. Usa il pulsante per aprirlo nel tuo Home Assistant, oppure segui i passaggi per la tua versione qui sotto.

[Apri la tua istanza di Home Assistant e apri questo repository nel Marketplace.](https://my.home-assistant.io/redirect/marketplace_repository/?owner=matteoraf&repository=ha-inim_smartliving_alarm_panel&category=integration){ .md-button }

Il pulsante apre il repository nel Marketplace con Home Assistant 2026.11 o successivo, e in HACS con le versioni precedenti (HACS deve essere installato). Scarica lì **Inim SmartLiving Alarm** e riavvia Home Assistant.

=== "Marketplace (2026.11 o successivo)"

    1.  Vai in **Impostazioni** → **Marketplace**.
    2.  Aggiungi `https://github.com/matteoraf/ha-inim_smartliving_alarm_panel` come repository personalizzato di tipo **Integrazione**. Per aggiungere repository personalizzati serve il collegamento del Marketplace a GitHub.
    3.  Apri **Inim SmartLiving Alarm** nel Marketplace e installalo.
    4.  Riavvia Home Assistant.

=== "HACS (da 2026.1 a 2026.10)"

    1.  In HACS, apri il menu (⋮) → **Repository personalizzati**.
    2.  Aggiungi `https://github.com/matteoraf/ha-inim_smartliving_alarm_panel` con tipo **Integrazione**.
    3.  Cerca **Inim SmartLiving Alarm** e scaricalo.
    4.  Riavvia Home Assistant.

=== "Manuale"

    1.  Scarica la cartella `inim_smartliving_alarm` dell'ultima versione.
    2.  Copiala nella tua cartella `config/custom_components`.
    3.  Riavvia Home Assistant.

Poi [aggiungi la tua centrale](setup.md#adding-a-panel).

## Versioni pre-release { #preview-versions }

Le versioni come `0.4.0a1` (anteprima) o `0.4.0b1` (beta) sono pubblicate come *pre-release* su GitHub. Vengono proposte solo a chi le chiede.

!!! warning "Prima fai un backup"

    **Fai un backup di Home Assistant prima di installare un'anteprima o una beta** (**Impostazioni** → **Sistema** → **Backup**). Un'anteprima o una beta aggiorna la voce di configurazione a un formato più recente, che le versioni stabili 0.3.x non comprendono del tutto: installata sopra, la 0.3.x crea di nuovo le entità per 10 aree, 50 zone e 30 scenari (anche quelli che non avevi importato), non riesce a collegarsi alle schede SmartLAN/G e lascia non disponibili le entità che ha solo la 0.4 (come i selettori della modalità di inserimento, le uscite e i sensori diagnostici). Per tornare a una versione stabile, ripristina quel backup; così si annulla anche ogni altra modifica fatta in Home Assistant dopo il backup. Anche per tornare dalla 0.5 alla 0.4 serve quel backup: la 0.4 non carica la voce di configurazione della 0.5.

=== "Marketplace (2026.11 o successivo)"

    Nella pagina dell'integrazione nel Marketplace, apri il menu (⋮) e scegli **Includi le versioni preliminari** per ricevere le pre-release, oppure **Installa un'altra versione** e scegli la versione.

=== "HACS (da 2026.1 a 2026.10)"

    Attiva l'interruttore **Pre-release** del repository (è disabilitato per impostazione predefinita: abilitalo prima), oppure usa **Scarica di nuovo** nella pagina dell'integrazione e scegli la versione.

=== "Manuale"

    Scarica la cartella `inim_smartliving_alarm` dalla pre-release su GitHub.

Segnala i problemi di un'anteprima o di una beta con il modulo [Beta report](https://github.com/matteoraf/ha-inim_smartliving_alarm_panel/issues/new?template=beta_report.yml), che chiede il modello della centrale, la versione del firmware e il tipo di scheda (SmartLAN/SI o SmartLAN/G).
