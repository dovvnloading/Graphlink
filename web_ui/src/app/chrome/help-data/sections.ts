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

export const HELP_SECTIONS: HelpSection[] = [
  {
    name: "Start Here",
    description: "What this app is, and the loop everything else hangs off.",
    subsections: [
      {
        title: "The Idea",
        items: [
          {
            action: "A graph, not a transcript",
            description:
              "Every prompt, reply, note, chart and tool run is a node on a canvas, connected to whatever it came from. A chat window forces one line of thought and buries the rest above the scroll. Here the alternatives stay side by side, and the connections record which answer came from which question.",
          },
          {
            action: "The selected node is the context",
            description:
              "Whatever you have selected is the parent of whatever you do next. Select a reply and send a message, and the new node hangs off that reply with its history. Select a different one and you have branched. This is the single rule the rest of the app is built on - the composer's placeholder tells you which node it is about to answer.",
          },
          {
            action: "It is a project file, not a session",
            description:
              "Nodes, frames, containers, pins, notes and plugin state persist, and a graph reopens from the Library exactly as you left it. Work that spans a week belongs here more than work that spans five minutes.",
          },
        ],
      },
      {
        title: "Your First Ten Minutes",
        items: [
          {
            action: "Make a node",
            description:
              "Double-click empty canvas. Or just type in the composer at the bottom and send.",
          },
          {
            action: "Branch off an answer",
            description:
              "Click the reply you liked, then send your follow-up. Click a different reply and send a different follow-up. You now have two branches from the same point, and neither overwrote the other.",
          },
          {
            action: "Reach for a specialist",
            description:
              "Plugins in the toolbar adds a node that does something a plain reply cannot - research the web with citations, run Python, render HTML, draft a document. It attaches to the node you have selected.",
          },
          {
            action: "When you lose the thread",
            description:
              "Fit All frames everything on the canvas. The command palette finds any action by name. Both are faster than hunting.",
            keys: ["Ctrl+K"],
          },
        ],
      },
      {
        title: "Three Ways People Use It",
        items: [
          {
            action: "Compare instead of decide early",
            description:
              "Ask the same question three ways from the same parent, leave all three on the canvas, and choose once you can see them together. Compare Branches puts two side by side; Synthesize Branches merges what survived.",
            keys: ["Ctrl+Shift+C", "Ctrl+Shift+M"],
          },
          {
            action: "Debug with the code that actually ran",
            description:
              "Keep the failing code in a Virtual Environment Runner node, the reasoning in its own branch, and the fix attempts as siblings. The run output stays attached to the attempt that produced it, which is the thing a chat log loses first.",
          },
          {
            action: "Write long, in pieces",
            description:
              "An Artifact / Drafter node holds a living document while the branches around it argue about sections. The draft is one node you keep editing, not thirty replies you have to reassemble.",
          },
        ],
      },
    ],
  },
  {
    name: "The Canvas",
    description: "Moving around, selecting, branching, and what the gestures actually do.",
    subsections: [
      {
        title: "Moving Around",
        items: [
          {
            action: "Pan",
            description:
              "Drag empty canvas with the left or middle button. Pan speed is adjustable - View, then Canvas, then Pan speed - which matters more than it sounds once a graph is bigger than a screen.",
          },
          {
            action: "Zoom",
            description:
              "The mouse wheel zooms. Holding Ctrl while you scroll does the same thing, so either habit works. The toolbar's zoom readout shows the current level; click it to go back to 100%.",
          },
          {
            action: "Fit All",
            description:
              "Reframes the view around everything on the canvas. The one control worth learning first, because it is the way back from anywhere.",
          },
          {
            action: "Minimap",
            description:
              "Bottom right. Click anywhere in it to jump there. It earns its keep at about the point the graph stops fitting on one screen.",
          },
        ],
      },
      {
        title: "Selecting",
        items: [
          {
            action: "Click to select",
            description: "One node. This also sets the context for your next message.",
          },
          {
            action: "Shift-drag to rubber-band",
            description:
              "Hold Shift and drag across empty canvas to select everything inside the rectangle. Shift is what distinguishes this from panning - without it, dragging the background moves the view.",
          },
          {
            action: "Ctrl-arrow to walk the branch",
            description:
              "Moves the selection to the parent, child or sibling of the current node. Reviewing a branch this way is considerably faster than aiming at each card.",
            keys: ["Ctrl+←", "Ctrl+→"],
          },
          {
            action: "Delete removes the selection",
            description:
              "Whatever is selected, however many. Undo brings it back - the undo stack lives on the backend and survives more than you would expect.",
            keys: ["Ctrl+Z"],
          },
        ],
      },
      {
        title: "Branching",
        items: [
          {
            action: "History is inherited, not shared",
            description:
              "A branch carries the conversation down its own path from its anchor node. Two siblings from the same parent see the same history behind them and nothing of each other, which is what makes them a fair comparison.",
          },
          {
            action: "Regenerate",
            description:
              "Re-asks from the same parent with the same context. Use it when the answer was wrong rather than the question.",
          },
          {
            action: "Hide other branches",
            description:
              "A chat node's own menu can dim everything outside its path, for when the canvas has fanned out further than you can hold in your head. View, then Focus, then Focus Accepted Paths does the same thing graph-wide using branch status.",
          },
          {
            action: "Mark what survived",
            description:
              "Branches can be set Accepted, Rejected or Superseded. Nothing is deleted - the rejected attempt stays on the canvas as a record of what you tried - but the filters and the focus lens can then dim it out of the way.",
          },
        ],
      },
    ],
  },
  {
    name: "Node Types",
    description: "What each kind of node is for, and when to reach for it.",
    subsections: [
      {
        title: "Conversation",
        items: [
          {
            action: "Chat",
            description: "A prompt and its reply. The default, and most of any graph.",
          },
          {
            action: "Conversation",
            description:
              "A self-contained linear thread in one node, for the stretches where branching is not the point and you just want a normal back-and-forth.",
          },
          {
            action: "Thinking",
            description:
              "The model's reasoning, kept separate from its answer so you can read the working without it crowding the result. Produced when the Reasoning level in the composer is above Off.",
          },
          {
            action: "Code",
            description:
              "A code block split out of a reply, so it can be copied, run or edited without dragging the prose along with it.",
          },
        ],
      },
      {
        title: "Content",
        items: [
          {
            action: "Note",
            description:
              "Your own text, attached wherever you put it. The command palette makes one; there is deliberately no shortcut, because a note you meant to make is worth two seconds.",
          },
          {
            action: "Document and Image",
            description:
              "Attached files become nodes. Documents open in a reading pane with a table of contents and in-document search; images open at full size.",
          },
          {
            action: "Chart",
            description:
              "Generated from data in the conversation and rendered live, not as a picture of a chart. The underlying data stays inspectable.",
          },
          {
            action: "Artifact",
            description:
              "A living Markdown document with a source and preview split. Meant to be revised in place across many turns.",
          },
        ],
      },
      {
        title: "Agents and Tools",
        items: [
          {
            action: "Plan",
            description:
              "The Builder's checklist. It shows the steps, which one is running, what has been spent against the budgets, and every tool call the run has made.",
          },
          {
            action: "Agent",
            description:
              "The workspace agent's card - its task, the folder it is bound to, its turns, and any approval it is currently waiting on.",
          },
          {
            action: "Web Research, Gitlink, Virtual Environment Runner, HTML Renderer",
            description:
              "Plugin nodes. Each has its own controls and its own state, and each stays on the canvas as a record of what it did.",
          },
        ],
      },
    ],
  },
  {
    name: "Finding Things",
    description: "Six different search surfaces, and which one you actually want.",
    subsections: [
      {
        title: "Search",
        items: [
          {
            action: "Command palette",
            description:
              "Every action in the app, by name. If you cannot remember where a control lives, this is faster than remembering.",
            keys: ["Ctrl+K"],
          },
          {
            action: "Canvas search",
            description:
              "Text inside the nodes of the graph you have open. Matches are highlighted in place and you can step between them.",
            keys: ["Ctrl+F"],
          },
          {
            action: "Quick switcher",
            description: "Jump to another saved graph by fuzzy-matching its title.",
            keys: ["Ctrl+P"],
          },
          {
            action: "Global Search",
            description:
              "Across every workspace and every ingested document at once - the one to use when you know you wrote it somewhere and not where.",
          },
          {
            action: "Knowledge",
            description:
              "Searches your ingested knowledge base and returns the exact passage, at the exact offset, with a link back to the source where one exists. Lexical always; also vector-based once an embedding model is configured.",
          },
        ],
      },
      {
        title: "Orientation",
        items: [
          {
            action: "Navigation pins",
            description:
              "Drop a pin on a spot worth returning to, give it a name, and jump back from the Pins list. Pins are marked on the canvas itself, so they are findable without opening the list.",
          },
          {
            action: "Filters",
            description:
              "View, then Focus. Filter by node kind or by branch status to dim everything you are not looking at. Nothing is hidden or moved - the filter is a lens, and Clear takes it off.",
          },
        ],
      },
    ],
  },
  {
    name: "Organizing",
    description: "Grouping, tidying, and making a large canvas legible.",
    subsections: [
      {
        title: "Structure",
        items: [
          {
            action: "Frames",
            description: "Wrap a selection in a labelled boundary. The nodes stay independent.",
            keys: ["Ctrl+G"],
          },
          {
            action: "Containers",
            description:
              "Group nodes so they move together. Use a container when the group is one thing; use a frame when it is several things about one subject.",
            keys: ["Ctrl+Shift+G"],
          },
          {
            action: "Titles and colors",
            description:
              "Rename any node, and colour frames and containers. Colour is the fastest legend a large canvas can have, and it costs nothing to change later.",
          },
          {
            action: "Organize",
            description:
              "Lays the graph out as a tree. It moves things - if you have arranged the canvas deliberately, this will undo that arrangement, and Undo will bring it back.",
          },
        ],
      },
      {
        title: "Appearance",
        items: [
          {
            action: "The View panel",
            description:
              "Canvas, Grid, Connections, Node Font and Focus. Pan speed, snapping, grid spacing and style, connection routing, node typography and the focus filters all live here, with Reset to Defaults pinned at the bottom.",
          },
          {
            action: "Snapping",
            description:
              "Snap to Grid lands dragged nodes on grid lines. Smart Guides aligns them to their neighbours instead. Both are off by default; pick one rather than both.",
          },
          {
            action: "Quieter connections",
            description:
              "Fade Connections dims every line except the one under the pointer. On a graph with a hundred edges this is the difference between a diagram and a hairball.",
          },
          {
            action: "Collapse",
            description:
              "Most node types collapse to a title bar. A branch that is finished but still worth keeping connected is exactly what collapse is for. Nodes also collapse automatically when you zoom far enough out.",
          },
        ],
      },
    ],
  },
  {
    name: "Plugins",
    description: "Specialist nodes, grouped the way the picker groups them.",
    subsections: [
      {
        title: "Branch Foundations",
        items: [
          {
            action: "System Prompt",
            description:
              "Overrides the system prompt for everything downstream of it. One branch can run under different instructions than its siblings, which is the cheapest A/B test in the app.",
          },
          {
            action: "Conversation Node",
            description: "A self-contained linear chat, for when you do not want to branch.",
          },
        ],
      },
      {
        title: "Reasoning and Research",
        items: [
          {
            action: "Web Research",
            description:
              "Searches, retrieves and summarizes web sources with citations, under a bounded network policy. Results carry their sources, so a claim can be checked rather than taken.",
          },
        ],
      },
      {
        title: "Build and Execution",
        items: [
          {
            action: "Gitlink",
            description:
              "Loads a GitHub repository as structured context, prepares file-level changes, and writes nothing until you approve the diff.",
          },
          {
            action: "Virtual Environment Runner",
            description:
              "Runs Python in an isolated virtualenv with per-node requirements. Read the isolation claim precisely: it isolates installed packages, not the operating system. Code runs with your user account's privileges.",
          },
          {
            action: "HTML Renderer",
            description: "Renders HTML from a parent node so you can see the page, not the markup.",
          },
        ],
      },
      {
        title: "Workflow and Drafting",
        items: [
          {
            action: "Artifact / Drafter",
            description:
              "A split-pane node for drafting and refining a Markdown document over many turns.",
          },
        ],
      },
    ],
  },
  {
    name: "The Builder",
    description: "An agent that plans a job as a checklist, then builds it on the canvas.",
    subsections: [
      {
        title: "Starting a Build",
        items: [
          {
            action: "Describe the goal",
            description:
              "A sentence or two of what you want built. The Builder turns it into a plan you can read and edit before anything runs - the plan is the point, and it is worth reading.",
          },
          {
            action: "Recipes",
            description:
              "A saved goal and step list. Starting from one skips the planning pass entirely, because the checklist already exists. You can save any finished build as a recipe and delete your own again later.",
          },
          {
            action: "Co-pilot or Autopilot",
            description:
              "Co-pilot asks before every mutating step. Autopilot does not - it creates nodes and executes code without stopping, inside the budgets. Network access still asks either way. Choose Autopilot when you have read the plan and believe it.",
          },
          {
            action: "Budgets are hard limits",
            description:
              "Quick, Standard and Extended set steps, tokens and wall-clock time; the exact numbers are under Set exact limits. A breach pauses the build rather than failing it, so a paused run can be resumed once you have decided it is worth more.",
          },
        ],
      },
      {
        title: "While It Runs",
        items: [
          {
            action: "Watch the checklist",
            description:
              "The running step is highlighted on the plan card, spent budgets are shown against their limits, and every tool call the run has made is listed - including the ones that failed.",
          },
          {
            action: "Edit the plan",
            description:
              "Steps can be retitled, reordered and removed while the build is startable or paused. Steps that have already run are history and cannot be rewritten.",
          },
          {
            action: "Stopping is permanent",
            description:
              "Pausing and interruptions can be resumed. Stop cannot - a stopped build is over, and the way forward is Undo build or a new one. This is deliberate, and the card says so before you press it.",
          },
          {
            action: "Undo build",
            description:
              "Reverses what the build did, back as far as the first thing you changed yourself afterwards. Your own later edits survive.",
          },
        ],
      },
    ],
  },
  {
    name: "The Agent",
    description: "An agent that works inside a real folder on your machine.",
    subsections: [
      {
        title: "Setting One Up",
        items: [
          {
            action: "Task and workspace",
            description:
              "Describe the job, then choose where it happens. The default is a private scratch folder. Choosing one of your own folders is what grants access to it - the agent cannot reach outside the folder it is bound to.",
          },
          {
            action: "What it can do",
            description:
              "Read and write files in its workspace, run shell commands and Python there, and search your knowledge base. It works in turns and reports back on the canvas.",
          },
          {
            action: "Turn budget",
            description:
              "A hard cap on how many turns one task gets. The run stops when it is reached, which is the backstop against an agent that has misunderstood the job and is busy being thorough about it.",
          },
          {
            action: "AGENTS.md",
            description:
              "If the workspace has one, the agent reads it. Project conventions belong there rather than in every task description.",
          },
        ],
      },
      {
        title: "Working With It",
        items: [
          {
            action: "Approvals",
            description:
              "Anything that changes your machine asks first, showing the exact command or file write. Deny is the focused default. Some tools can be approved for the whole session; the ones that run arbitrary code cannot, by design.",
          },
          {
            action: "It can ask you things",
            description:
              "When the agent needs a decision it parks and waits, and the card gives you a box to answer in. A parked agent is not a stuck one.",
          },
          {
            action: "Follow up",
            description:
              "Send another instruction to a finished agent and it picks up with its workspace and history intact.",
          },
          {
            action: "Agent or Builder?",
            description:
              "The Builder builds a graph - nodes, charts, documents, on the canvas. The Agent changes files in a folder. If the output is something you want to read here, use the Builder; if it is something you want on disk, use the Agent.",
          },
        ],
      },
    ],
  },
  {
    name: "Models and Settings",
    description: "Providers, per-task routing, and the settings worth knowing about.",
    subsections: [
      {
        title: "Where Models Come From",
        items: [
          {
            action: "Ollama and Llama.cpp",
            description:
              "Local models, each with its own Settings page. Nothing leaves the machine. Both scan for installed models rather than making you type an identifier.",
          },
          {
            action: "API Endpoint",
            description:
              "OpenAI-compatible providers and Gemini. This is also the path that image generation runs on - local providers have none.",
          },
          {
            action: "Per-task routing",
            description:
              "Chat, chat naming, chart generation, web validation and web summarization can each use a different model, and the API path adds image generation. The naming model is the one to make cheap: it writes titles and runs constantly.",
          },
          {
            action: "Routing preference",
            description:
              "Cheapest Capable, Fastest or Best Quality, for tasks that are set to choose automatically rather than pinned to a model.",
          },
        ],
      },
      {
        title: "Worth Knowing",
        items: [
          {
            action: "Reasoning level",
            description:
              "Off, Low, Medium or High, in the composer, per message. Higher levels think longer and cost more; High on a question that did not need it is the most common way to waste tokens here.",
          },
          {
            action: "Token counter",
            description:
              "Estimates what the draft in the composer will cost before you send it. An estimate, not an invoice.",
          },
          {
            action: "Resource limits",
            description:
              "Caps on what executed code may consume. They apply to plugin runs, sandboxes and agents alike, including under Autopilot.",
          },
          {
            action: "MCP servers and integrations",
            description:
              "Connect external tools through their own Settings pages. What a connected server exposes becomes available to agents - worth knowing before connecting one you have not read.",
          },
          {
            action: "Theme",
            description: "Match System, Light or Dark. The canvas follows.",
          },
          {
            action: "Optional dependencies",
            description:
              "PDF and DOCX handling and HTML preview need libraries that may not be installed. When one is missing the app names it rather than failing quietly.",
          },
        ],
      },
    ],
  },
  {
    name: "Saving and Output",
    description: "Persistence, attachments, and getting work back out.",
    subsections: [
      {
        title: "Saving",
        items: [
          {
            action: "Save",
            description:
              "Writes the current graph to the Library. Work is also saved in the background as you go, so Save is a bookmark more than a rescue.",
            keys: ["Ctrl+S"],
          },
          {
            action: "Library",
            description:
              "Every saved graph, grouped by when you touched it, with a preview of its last message. Rename and delete are per row.",
            keys: ["Ctrl+L"],
          },
          {
            action: "It is all local",
            description:
              "Graphs, attachments and knowledge are stored on your machine. Nothing is uploaded except what you send to whichever model provider you have configured.",
          },
        ],
      },
      {
        title: "In and Out",
        items: [
          {
            action: "Attachments",
            description:
              "Drop files onto the canvas or attach them in the composer. Text, documents, code and images all become nodes you can branch from.",
          },
          {
            action: "Export PNG",
            description:
              "The whole canvas as an image - the fastest way to show someone the shape of a project without giving them the project.",
          },
          {
            action: "Copy anything",
            description:
              "Code blocks, replies and document text all copy out cleanly. A graph is a place to think, not a place work gets trapped.",
          },
        ],
      },
    ],
  },
  {
    name: "Keyboard",
    description: "Every global shortcut. All of them are Ctrl on Windows and Linux, Cmd on macOS.",
    subsections: [
      {
        title: "Project",
        items: [
          { action: "New chat", description: "Starts a fresh graph.", keys: ["Ctrl+T"] },
          { action: "Open the Library", description: "Your saved graphs.", keys: ["Ctrl+L"] },
          { action: "Save", description: "Writes the current graph to the Library.", keys: ["Ctrl+S"] },
        ],
      },
      {
        title: "Finding",
        items: [
          { action: "Command palette", description: "Every action, by name.", keys: ["Ctrl+K"] },
          { action: "Search this graph", description: "Text inside the nodes on screen.", keys: ["Ctrl+F"] },
          { action: "Quick switcher", description: "Jump to another saved graph.", keys: ["Ctrl+P"] },
        ],
      },
      {
        title: "Editing",
        items: [
          {
            action: "Undo",
            description: "The undo stack is owned by the backend, so it outlasts a lot.",
            keys: ["Ctrl+Z"],
          },
          {
            action: "Redo",
            description: "Both spellings are wired - use whichever your hands already know.",
            keys: ["Ctrl+Shift+Z", "Ctrl+Y"],
          },
          {
            action: "Frame the selection",
            description: "A labelled boundary around what is selected.",
            keys: ["Ctrl+G"],
          },
          {
            action: "Container from the selection",
            description: "A group that moves as one.",
            keys: ["Ctrl+Shift+G"],
          },
        ],
      },
      {
        title: "Branches",
        items: [
          {
            action: "Move through the branch",
            description: "Parent, child and siblings of the selected node.",
            keys: ["Ctrl+←", "Ctrl+→"],
          },
          { action: "Compare branches", description: "Two side by side.", keys: ["Ctrl+Shift+C"] },
          {
            action: "Synthesize branches",
            description: "Merge what survived comparison into one result.",
            keys: ["Ctrl+Shift+M"],
          },
        ],
      },
      {
        title: "In a Text Field",
        items: [
          {
            action: "Shortcuts stand down",
            description:
              "While a text field has focus, editing keys go to the field. Ctrl+Z undoes your typing, not the graph. The exceptions are deliberate: Ctrl+S saves and Ctrl+K opens the palette from anywhere, because both are reflexes worth honouring mid-sentence.",
          },
        ],
      },
    ],
  },
];
