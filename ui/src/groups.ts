/**
 * What a fixture group is called on screen.
 *
 * "corner movers" is what the rig file calls them; "Movers" is what a person
 * calls them at 2am. Falls through to the raw tag for anything unrecognised, so
 * a new rig's groups still appear rather than vanishing.
 *
 * One module because this map was copy-pasted verbatim into LookPicker, Show
 * and Bright. Three copies of a display name is three places for a rig's groups
 * to be labelled differently on three tabs of the same console.
 */
const GROUP_LABELS: Record<string, string> = {
  "corner movers": "Movers", movers: "Movers", pinspots: "Pinspots",
  pars: "Pars", bars: "Bars",
};

// Own keys only: a plain object answers "constructor" and "toString" from its
// prototype, and a rig tagged with either got a function back as its label.
export const groupLabel = (g: string) => (Object.hasOwn(GROUP_LABELS, g) ? GROUP_LABELS[g]! : g);
