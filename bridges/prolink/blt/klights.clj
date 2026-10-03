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
;;;;   /klights/v1/phrase i deck, s label, f beats_into, f beats_left
;;;;                      -- the tempo master's phrase, from the rekordbox
;;;;                      analysis on the DJ's USB: on every change and every
;;;;                      bar. An empty label: the track has no phrase analysis.
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

(def klights-phrase-labels
  "rekordbox's phrase names by mood and kind -- the same table as
  bridges/rekordbox/anlz.py, so a guest's phrase reads exactly as a prepped
  one does and a template set matches both the same way."
  {:mid {1 "Intro" 2 "Verse 1" 3 "Verse 2" 4 "Verse 3" 5 "Verse 4"
         6 "Verse 5" 7 "Verse 6" 8 "Bridge" 9 "Chorus" 10 "Outro"}
   :low {1 "Intro" 2 "Verse 1" 3 "Verse 1" 4 "Verse 1" 5 "Verse 2"
         6 "Verse 2" 7 "Verse 2" 8 "Bridge" 9 "Chorus" 10 "Outro"}})

(defn klights-phrase-label
  "The label rekordbox shows. High-mood numbering is not in `kind` but in
  three flag bytes (anlz.adoc, \"High mood phrase variants\")."
  [mood kind k1 k2 k3]
  (if (= mood :high)
    (case (int kind)
      1 (if (= k1 1) "Intro 1" "Intro 2")
      2 (cond (= k2 1) "Up 3" (= k3 1) "Up 2" :else "Up 1")
      3 "Down"
      5 (if (= k1 1) "Chorus 1" "Chorus 2")
      6 (if (= k1 1) "Outro 1" "Outro 2")
      (str "Phrase " kind))
    (get-in klights-phrase-labels [mood (int kind)] (str "Phrase " kind))))

(defn klights-id
  "A Kaitai enum, or a plain number, as a number."
  [v]
  (if (number? v) (long v) (long (.id v))))

(defn klights-song-structure
  "The phrases of the track `player` has loaded, from the rekordbox analysis
  on the DJ's own USB (the PSSI tag of the .EXT file), as
  [[start-beat end-beat label] ...] in beat numbers -- or nil when the track
  has none (exported by rekordbox 5, or never analysed). Cached per track, so
  the 25 Hz tick is a map lookup."
  [player rekordbox-id]
  (let [k [player rekordbox-id]]
    (if-let [hit (get-in @globals [:klights-structure k])]
      (:phrases hit)
      (let [tag (.getLatestTrackAnalysisFor
                 (org.deepsymmetry.beatlink.data.AnalysisTagFinder/getInstance)
                 (int player) ".EXT" "PSSI")
            phrases
            (when tag
              (let [body    (.body tag)
                    mood    (keyword (clojure.string/lower-case (str (.mood body))))
                    entries (vec (.entries body))
                    ends    (concat (map #(.beat %) (rest entries))
                                    [(.endBeat body)])]
                (mapv (fn [e end]
                        [(.beat e) end
                         (klights-phrase-label mood (klights-id (.kind e))
                                               (.k1 e) (.k2 e) (.k3 e))])
                      entries ends)))]
        ;; Only a found analysis is cached: it can arrive a moment after the
        ;; track loads, and a nil remembered would stick for the whole track.
        (when phrases
          (swap! globals assoc :klights-structure {k {:phrases phrases}}))
        phrases))))

(defn klights-send-phrase
  "Where the tempo master is in its phrase, if its track has phrases: sent
  when the phrase changes and every bar, so an engine started mid-phrase
  catches up within a bar."
  [client player rekordbox-id beat]
  (let [phrases (klights-song-structure player rekordbox-id)
        here    (some (fn [[s e _ :as p]] (when (and (<= s beat) (< beat e)) p))
                      phrases)
        k       [player rekordbox-id (first here)]
        [last-k last-beat] (:klights-phrase-at @globals)]
    (when (and (pos? beat)
               (or (not= k last-k) (>= (Math/abs (- beat (or last-beat 0))) 4)))
      (let [[s e label] here]
        (overtone.osc/osc-send client "/klights/v1/phrase"
                               (int player)
                               (str (or label ""))
                               (float (if here (- beat s) 0))
                               (float (if here (- e beat) 0))))
      (swap! globals assoc :klights-phrase-at [k beat]))))

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
                                   (int (if (.isOnAir master) 1 0))))
          (klights-send-phrase klights-client player (.getRekordboxId master)
                               (.getBeatNumber master)))))
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
         :klights-last :klights-phrase-at :klights-structure))


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
                  (org.deepsymmetry.beatlink.data.SignatureFinder/getInstance)
                  (org.deepsymmetry.beatlink.data.AnalysisTagFinder/getInstance)]]
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
