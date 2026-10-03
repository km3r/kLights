;;;; beat-link-trigger -> kLights
;;;;
;;;; Expressions that make beat-link-trigger (BLT) send the kLights engine
;;;; which track the tempo master is playing and where in it, plus tempo and
;;;; bar phase on every beat. Paste each section into the BLT expression it
;;;; names (Triggers window, File menu). See bridges/prolink/README.md.
;;;;
;;;; What it sends, to the engine's --sync-port (the `/klights/v1` namespace in
;;;; engine/sync.py, decoded strictly: exact argument count and kinds):
;;;;
;;;;   /klights/v1/pos    i deck, i playing, f time_s, f pitch, i beat_number,
;;;;                      i master, i on_air                 -- 25 times a second
;;;;   /klights/v1/track  i deck, i rekordbox_id, s signature, s title,
;;;;                      s artist, s album, f duration_s    -- on every change
;;;;   /bpm  f            the tempo master's effective tempo  -- every beat
;;;;   /beat f            its beat within the bar, 0-3        -- every beat
;;;;
;;;; Written against beat-link-trigger 8's documented expression API and the
;;;; beat-link Java API (VirtualCdj, TimeFinder, MetadataFinder,
;;;; SignatureFinder, BeatFinder), following the shape of BLT's own ArtNet
;;;; Timecode integration example. NOT YET RUN AGAINST HARDWARE: the golden
;;;; fixture in engine/tests/test_sync.py pins the exact bytes these must
;;;; produce, and the hardware checklist in docs/design/timecoded-shows.md is
;;;; how the first real run is judged.
;;;;
;;;; Position is only exact on CDJ-3000s, which report it every 30 ms. Older
;;;; players' position is estimated from beat packets by BLT and goes wrong on
;;;; loops and reverse play -- BLT's own documentation says so.


;;; ---- Shared Functions ------------------------------------------------------
;;; (Triggers window: File > Edit Shared Functions)

(defn klights-send-track
  "Who the given player's track is, in one message. Returns true if it was
  sent -- metadata can take a moment to arrive after a track loads, and the
  caller retries until it has."
  [client player]
  (let [metadata (.getLatestMetadataFor
                  (org.deepsymmetry.beatlink.data.MetadataFinder/getInstance) player)
        signature (.getLatestSignatureFor
                   (org.deepsymmetry.beatlink.data.SignatureFinder/getInstance) player)]
    (when metadata
      (overtone.osc/osc-send client "/klights/v1/track"
                             (int player)
                             (int (.rekordboxId (.trackReference metadata)))
                             (str (or signature ""))
                             (str (or (.getTitle metadata) ""))
                             (str (or (some-> (.getArtist metadata) .label) ""))
                             (str (or (some-> (.getAlbum metadata) .label) ""))
                             (float (.getDuration metadata)))
      true)))

(defn klights-tick
  "Runs :klights-hz times a second, off BLT's event threads: the tempo
  master's position, and its identity whenever the deck or track changes."
  []
  (try
    (let [{:keys [klights-client klights-last]} @globals
          master (.getTempoMaster virtual-cdj)]
      (when (and klights-client
                 (instance? org.deepsymmetry.beatlink.CdjStatus master))
        (let [player (.getDeviceNumber master)
              ident  [player (.getRekordboxId master)]
              millis (.getTimeFor time-finder player)]
          (when (and (not= ident klights-last)
                     (klights-send-track klights-client player))
            (swap! globals assoc :klights-last ident))
          ;; -1 means TimeFinder has no position yet (nothing loaded, or the
          ;; track is still being analysed). Send nothing rather than a lie.
          (when (>= millis 0)
            (overtone.osc/osc-send klights-client "/klights/v1/pos"
                                   (int player)
                                   (int (if (.isPlaying master) 1 0))
                                   (float (/ millis 1000.0))
                                   (float (org.deepsymmetry.beatlink.Util/pitchToMultiplier
                                           (.getPitch master)))
                                   (int (.getBeatNumber master))
                                   (int 1)
                                   (int (if (.isOnAir master) 1 0)))))))
    (catch Throwable t
      (timbre/warn t "kLights: position tick failed"))))

(defn klights-stop
  "Undo everything Came Online started. Safe to call twice."
  []
  (when-let [executor (:klights-executor @globals)]
    (.shutdown executor))
  (when-let [beats (:klights-beats @globals)]
    (.removeBeatListener (org.deepsymmetry.beatlink.BeatFinder/getInstance) beats))
  (when-let [client (:klights-client @globals)]
    (osc/osc-close client))
  (swap! globals dissoc :klights-client :klights-executor :klights-beats
         :klights-last))


;;; ---- Global Setup Expression -----------------------------------------------
;;; (Triggers window: File > Edit Global Setup Expression)
;;; Where the engine is listening. Change the port to the engine's --sync-port,
;;; and the host if the engine runs on another machine (then start the engine
;;; with --sync-bind and --sync-allow; see the README's Security section).

(swap! globals assoc
       :klights-host "127.0.0.1"
       :klights-port 9000
       :klights-hz   25)


;;; ---- Came Online Expression ------------------------------------------------

(let [client   (osc/osc-client (:klights-host @globals) (:klights-port @globals))
      executor (java.util.concurrent.Executors/newSingleThreadScheduledExecutor)
      beats    (reify org.deepsymmetry.beatlink.BeatListener
                 (newBeat [_ beat]
                   (when (.isTempoMaster beat)
                     (try
                       (overtone.osc/osc-send client "/bpm"
                                              (float (.getEffectiveTempo beat)))
                       (overtone.osc/osc-send client "/beat"
                                              (float (dec (.getBeatWithinBar beat))))
                       (catch Throwable t
                         (timbre/warn t "kLights: beat send failed"))))))]
  ;; The finders the tick reads. Starting one that is already running is
  ;; harmless; TimeFinder in particular is otherwise only running while the
  ;; Player Status window is open.
  (doseq [finder [(org.deepsymmetry.beatlink.data.TimeFinder/getInstance)
                  (org.deepsymmetry.beatlink.data.MetadataFinder/getInstance)
                  (org.deepsymmetry.beatlink.data.SignatureFinder/getInstance)]]
    (try (.start finder)
         (catch Throwable t (timbre/warn t "kLights: could not start" finder))))
  (.addBeatListener (org.deepsymmetry.beatlink.BeatFinder/getInstance) beats)
  (swap! globals assoc :klights-client client :klights-executor executor
         :klights-beats beats :klights-last nil)
  (.scheduleAtFixedRate executor klights-tick 0
                        (quot 1000 (:klights-hz @globals))
                        java.util.concurrent.TimeUnit/MILLISECONDS))


;;; ---- Going Offline Expression ----------------------------------------------

(klights-stop)


;;; ---- Global Shutdown Expression --------------------------------------------

(klights-stop)
