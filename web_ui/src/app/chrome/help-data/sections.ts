/**
 * The Help panel's content.
 *
 * HAND-MAINTAINED. This file used to open with "GENERATED - do not
 * hand-edit", inherited from the Qt-era extraction that first produced it
 * (the legacy HelpDialog.SECTION_DEFS in graphlink_system_dialogs.py, long
 * since deleted). That header stopped being true the first time someone
 * added a section by hand, and by the time it was audited the file had
 * grown from the 76 items it claimed to 88 - so the one instruction at the
 * top of the file was both false and discouraging the exact maintenance the
 * file was getting anyway. Edit it. Nothing generates it.
 *
 * WHAT THIS IS FOR. Someone opens Help because something on screen did not
 * do what they expected, or because they suspect the app can do a thing and
 * want to know where it lives. Both are answered by specifics: the real
 * label on the real control, the actual key, the actual limit. Neither is
 * answered by a paragraph about how the workspace empowers their workflow.
 *
 * SO: say the thing. Lead with the fact, not with a restatement of the
 * item's own title. Give the shortcut if there is one. Say where a feature
 * stops - what it will not do, what it cannot undo, what it costs - because
 * that is the part nobody can discover by clicking around, and it is the
 * part that makes the rest trustworthy. One sentence is a fine length. So
 * is four, when four are needed. What kills a reference page is every entry
 * being the same shape.
 *
 * KEEP IT TRUE. Every label here is quoted from the running app. When a
 * control is renamed, this file is part of the change, not a follow-up.
 * The audit that produced this rewrite found the Help panel confidently
 * describing a "Controls toggle" renamed to View, a Shift-drag zoom gesture
 * that had become rubber-band selection, and a "Graphlink-Web" plugin
 * shipping under the name Web Research - three things a reader would have
 * trusted and then failed to find.
 */

export interface HelpItem {
  action: string;
  description: string;
  /** Key chords for this item, each rendered as its own <kbd>. Two entries
   *  mean "either one works", which is a real case here (redo). */
  keys?: string[];
}

export interface HelpSubsection {
  title: string;
  items: HelpItem[];
}

export interface HelpSection {
  name: string;
  description: string;
  subsections: HelpSubsection[];
}

// JSON is the maintained content source. Type-check it here for the help
// contract tests; the runtime Help dialog fetches the JSON asset by URL
// only on its first opening, keeping thousands of words out of JS chunks.
import sections from "./sections.json";
export const HELP_SECTIONS: HelpSection[] = sections;
