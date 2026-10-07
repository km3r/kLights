import io.kaitai.struct.RandomAccessFileKaitaiStream;
import org.deepsymmetry.beatlink.CdjStatus;
import org.deepsymmetry.beatlink.data.BeatGrid;
import org.deepsymmetry.beatlink.data.DataReference;
import org.deepsymmetry.beatlink.data.SearchableItem;
import org.deepsymmetry.beatlink.data.SignatureFinder;
import org.deepsymmetry.beatlink.data.TrackMetadata;
import org.deepsymmetry.beatlink.data.WaveformDetail;
import org.deepsymmetry.cratedigger.Database;
import org.deepsymmetry.cratedigger.pdb.RekordboxAnlz;
import org.deepsymmetry.cratedigger.pdb.RekordboxPdb;

import java.io.File;
import java.nio.ByteBuffer;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.security.MessageDigest;
import java.util.Base64;
import java.util.Map;

/**
 * beat-link's own track signature, computed the way beat-link-trigger computes
 * it at a gig: the color waveform is the PWV5 tagged section of the .EXT (what
 * SignatureFinder gets from AnalysisTagFinder), the grid is the .DAT's, and on a
 * stick the title, artist and length come from beat-link's own
 * TrackMetadata(reference, Database, cueList) over the stick's export.pdb.
 *
 *   collection IN.tsv
 *       IN.tsv lines: id, b64 title, b64 artist or ~none~, duration, .DAT, .EXT
 *   usb STICK_ROOT
 *       every track in STICK_ROOT/PIONEER/rekordbox/export.pdb
 *
 * Prints, per track, tab-separated: id, b64 title, b64 artist or ~none~,
 * b64 album, duration, signature, sha1 of the PWV5 entries, sha1 of the beats
 * (each as two big-endian ints, as the signature hashes them), the beat count.
 * Run by check.py, which supplies the classpath.
 */
public class SigCheck {
    static final String NONE = "~none~";       // a track with no artist at all
    static final DataReference REF = new DataReference(1, CdjStatus.TrackSourceSlot.USB_SLOT, 1,
                                                       CdjStatus.TrackType.REKORDBOX);

    static String b64(String s) {
        return Base64.getEncoder().encodeToString(s.getBytes(StandardCharsets.UTF_8));
    }

    static String unb64(String s) {
        return new String(Base64.getDecoder().decode(s), StandardCharsets.UTF_8);
    }

    static String sha1(byte[] data) throws Exception {
        StringBuilder hex = new StringBuilder();
        for (byte b : MessageDigest.getInstance("SHA-1").digest(data)) {
            hex.append(String.format("%02x", b & 0xff));
        }
        return hex.toString();
    }

    /** signature, wave sha1, beats sha1, beat count -- or an ERROR line's tail. */
    static String compute(String title, SearchableItem artist, int duration, File dat, File ext) {
        try {
            RekordboxAnlz extFile = new RekordboxAnlz(new RandomAccessFileKaitaiStream(ext.getPath()));
            WaveformDetail wave = null;
            for (RekordboxAnlz.TaggedSection section : extFile.sections()) {
                if (section.body() instanceof RekordboxAnlz.WaveColorScrollTag) {
                    wave = new WaveformDetail(REF, section);
                }
            }
            if (wave == null) {
                return "NO-PWV5\t\t\t0";
            }
            BeatGrid grid = new BeatGrid(REF, new RekordboxAnlz(new RandomAccessFileKaitaiStream(dat.getPath())));
            String signature = SignatureFinder.getInstance().computeTrackSignature(
                    title, artist, duration, wave, grid);
            ByteBuffer data = wave.getData();
            byte[] waveBytes = new byte[data.remaining()];
            data.get(waveBytes);
            ByteBuffer beats = ByteBuffer.allocate(8 * grid.beatCount);
            for (int i = 1; i <= grid.beatCount; i++) {
                beats.putInt(grid.getBeatWithinBar(i));
                beats.putInt((int) grid.getTimeWithinTrack(i));
            }
            return signature + "\t" + sha1(waveBytes) + "\t" + sha1(beats.array()) + "\t" + grid.beatCount;
        } catch (Exception e) {
            return "ERROR " + e.toString().replace('\t', ' ') + "\t\t\t0";
        }
    }

    public static void main(String[] args) throws Exception {
        if (args.length == 2 && args[0].equals("collection")) {
            for (String line : Files.readAllLines(Path.of(args[1]), StandardCharsets.UTF_8)) {
                if (line.isEmpty()) continue;
                String[] f = line.split("\t", -1);
                SearchableItem artist = f[2].equals(NONE) ? null : new SearchableItem(0, unb64(f[2]));
                System.out.println(f[0] + "\t" + f[1] + "\t" + f[2] + "\t\t" + f[3] + "\t"
                        + compute(unb64(f[1]), artist, Integer.parseInt(f[3]),
                                  new File(f[4]), new File(f[5])));
            }
        } else if (args.length == 2 && args[0].equals("usb")) {
            File root = new File(args[1]);
            Database db = new Database(new File(root, "PIONEER/rekordbox/export.pdb"));
            for (Map.Entry<Long, RekordboxPdb.TrackRow> e : db.trackIndex.entrySet()) {
                int id = (int) (long) e.getKey();
                TrackMetadata md = new TrackMetadata(new DataReference(1, CdjStatus.TrackSourceSlot.USB_SLOT,
                        id, CdjStatus.TrackType.REKORDBOX), db, null);
                String rel = Database.getText(e.getValue().analyzePath());
                String extRel = rel.length() > 4 ? rel.substring(0, rel.length() - 4) + ".EXT" : rel;
                System.out.println(id + "\t" + b64(md.getTitle()) + "\t"
                        + (md.getArtist() == null ? NONE : b64(md.getArtist().label)) + "\t"
                        + (md.getAlbum() == null ? "" : b64(md.getAlbum().label)) + "\t"
                        + md.getDuration() + "\t"
                        + compute(md.getTitle(), md.getArtist(), md.getDuration(),
                                  new File(root, rel), new File(root, extRel)));
            }
            db.close();
        } else {
            System.err.println("usage: SigCheck collection IN.tsv | SigCheck usb STICK_ROOT");
            System.exit(2);
        }
    }
}
