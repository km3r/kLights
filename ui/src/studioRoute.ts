import { apiUrl } from "./useEngine";

/**
 * Studio's addresses, in the main bundle: the console decides from the hash
 * whether to load Studio at all, and links into it from the header and the
 * Track card.
 *
 *   #studio                      the track library
 *   #studio/routines             the routine library
 *   #studio/rekordbox[/<id>]     the rekordbox collection, or one playlist
 *                                (?find=<text> searches the whole collection)
 *   #studio/track/<id>           a track's timeline
 *   #studio/routine/<id>         a routine
 *
 * Studio used to be "the designer", at #designer -- and "design" is also the
 * console's own Design mode, which is how two different things came to share a
 * name. Old #designer links still work: they are rewritten to these.
 */

export type StudioRoute =
  | { view: "tracks" }
  | { view: "routines" }
  | { view: "rekordbox"; scope: string; find?: string }
  | { view: "track"; id: string }
  | { view: "routine"; id: string };

export function studioRoute(hash: string): StudioRoute | null {
  const [path = "", query = ""] = hash.replace(/^#/, "").split("?");
  const [root, what, ident] = path.split("/");
  if (root !== "studio") return null;
  if (what === "routines") return { view: "routines" };
  if (what === "rekordbox") {
    const find = new URLSearchParams(query).get("find") ?? undefined;
    return { view: "rekordbox", scope: ident || "all", ...(find ? { find } : {}) };
  }
  if (what === "track" && ident) return { view: "track", id: ident };
  if (what === "routine" && ident) return { view: "routine", id: ident };
  return { view: "tracks" };
}

/** An old designer address as Studio's, or null if it is not one. */
export function fromDesignerHash(hash: string): string | null {
  const raw = hash.replace(/^#/, "");
  if (raw !== "designer" && !raw.startsWith("designer/")) return null;
  const [, what, ident] = raw.split("/");
  if (what === "routine") return ident ? `#studio/routine/${ident}` : "#studio/routines";
  return what ? `#studio/track/${what}` : "#studio";
}

/** Studio opens in a tab of its own, and a second link reuses that tab. */
export const STUDIO_TARGET = "klights-studio";

/** A link into Studio from the console. It carries the token, so the new tab
 *  can save and drive the rig even when this page's address has lost it. */
export function studioHref(route = ""): string {
  return `${apiUrl(location.pathname)}#studio${route ? `/${route}` : ""}`;
}
