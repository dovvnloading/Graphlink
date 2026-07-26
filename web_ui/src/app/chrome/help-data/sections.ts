/* GENERATED - do not hand-edit. Source of truth was the legacy
 * HelpDialog.SECTION_DEFS in graphlink_ui_dialogs/graphlink_system_dialogs.py
 * (deleted in Phase 4's shim-collapse increment once this file existed) -
 * mechanically extracted (icon fields dropped per the Phase 4 scope note;
 * see doc/FRONTEND_WEB_MIGRATION_MASTER_PLAN.md), not hand-retyped, so the
 * 76 items/19 subsections/9 sections of copy carried over byte-exact.
 * This is now the single canonical source - nothing else reads this
 * content, per "adding a section requires no Python change." */

export interface HelpItem {
  action: string;
  description: string;
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
    "name": "Overview",
    "description": "What Graphlink is, how work is structured, and how a typical project moves from prompt to polished output.",
    "subsections": [
      {
        "title": "What Graphlink Does",
        "items": [
          {
            "action": "Visual AI Workspace",
            "description": "Graphlink turns prompts, replies, notes, charts, documents, code, and plugin runs into connected items on one canvas. Instead of losing work inside a single scrolling transcript, you can keep multiple lines of thought visible at once."
          },
          {
            "action": "Branch-Based Conversations",
            "description": "Every selected node becomes the anchor for what you do next. That makes it easy to fork alternatives, compare directions, and keep complex work organized as a family of branches instead of one long thread."
          },
          {
            "action": "Mixed Content Graph",
            "description": "A single project can contain chat nodes, code blocks, visible reasoning, attached files, generated images, notes, charts, and specialist tool nodes. The connections preserve where an idea came from and what it produced."
          },
          {
            "action": "Saved Project Space",
            "description": "Chats, plugin state, frames, containers, pins, and notes are persisted and can be reopened from the Library. Graphlink is designed for ongoing project work, not just one-off prompts."
          }
        ]
      },
      {
        "title": "Typical Session Flow",
        "items": [
          {
            "action": "Start or Resume",
            "description": "Open a recent project from the Library, or press Ctrl+T to start a new chat. The app is comfortable for quick ideation and for returning to long-running work."
          },
          {
            "action": "Build a Branch",
            "description": "Send a prompt, review the result, then select the exact node you want to continue from. Each selection creates a deliberate branch point so you can refine or challenge a result without overwriting earlier work."
          },
          {
            "action": "Add Specialist Nodes",
            "description": "When a branch needs something more than a plain reply, open Plugins to add reasoning, web research, coding, sandbox execution, drafting, HTML rendering, or branch comparison tools."
          },
          {
            "action": "Capture the Outcome",
            "description": "Use notes, explainers, takeaways, charts, exports, and document views to turn raw responses into a clearer project record. The goal is a canvas you can revisit and understand later."
          }
        ]
      }
    ]
  },
  {
    "name": "Canvas & Branching",
    "description": "How selection, context inheritance, branching, and side-by-side exploration work inside the graph.",
    "subsections": [
      {
        "title": "How Branches Work",
        "items": [
          {
            "action": "Active Context Node",
            "description": "The node you select becomes the parent for your next message or specialist tool. The input placeholder updates to show what you are responding to, which helps you stay oriented in large graphs."
          },
          {
            "action": "Parent and Child History",
            "description": "Branches inherit the relevant conversation history from their anchor node. Sibling branches can explore different ideas from the same point without stomping on each other."
          },
          {
            "action": "Focus One Branch",
            "description": "Chat nodes can temporarily hide other branches so you can concentrate on one path. This is especially useful once a graph starts to fan out into several competing directions."
          },
          {
            "action": "Regenerate from the Same Context",
            "description": "Assistant responses can be regenerated from their parent branch when you want a fresh answer without manually rebuilding the surrounding context."
          }
        ]
      },
      {
        "title": "Working on the Canvas",
        "items": [
          {
            "action": "Select and Move",
            "description": "Click a node to select it, drag on empty space to rubber-band select, and move one item or a whole group together. The graph behaves like a visual workspace, not a locked transcript."
          },
          {
            "action": "Collapse Dense Areas",
            "description": "Most major node types can collapse to keep the canvas readable. Collapse is especially helpful when a branch is stable but still needs to stay connected to the rest of the project."
          },
          {
            "action": "Keep Notes Beside the Work",
            "description": "Notes live alongside AI nodes rather than inside them. Use them for decisions, TODOs, pasted snippets, executive summaries, or anything that should stay visible but should not be sent back to the model."
          },
          {
            "action": "Compare Alternatives",
            "description": "When two branch tips represent different approaches, Branch Lens can inspect both branches and explain where their logic, code direction, and intent diverge."
          }
        ]
      }
    ]
  },
  {
    "name": "Nodes & Content",
    "description": "The main node types you will see on the canvas and what each one is best at.",
    "subsections": [
      {
        "title": "Core Conversation Nodes",
        "items": [
          {
            "action": "Chat Nodes",
            "description": "User and assistant chat nodes are the backbone of the application. Selecting one lets you continue exactly that line of thought, which makes branching feel intentional instead of accidental."
          },
          {
            "action": "Code Nodes",
            "description": "When a response includes fenced code, Graphlink breaks it into dedicated code nodes. Those nodes can be copied, exported, regenerated through their parent reply, or used as a starting point for coding tools."
          },
          {
            "action": "Thinking Nodes",
            "description": "If a model returns visible reasoning or analysis, the app stores it as a separate thinking node. This keeps long reasoning trails available without burying the main answer."
          },
          {
            "action": "Document and Image Nodes",
            "description": "Attachments become their own nodes on the branch so the source material remains visible next to the prompt that used it. That makes it easier to audit what the model actually saw."
          }
        ]
      },
      {
        "title": "Supporting Content",
        "items": [
          {
            "action": "Notes",
            "description": "Manual notes, takeaways, explainer notes, and multi-node summary notes all live as movable note items. They are ideal for turning raw model output into cleaner project knowledge."
          },
          {
            "action": "Charts",
            "description": "The app can turn text-derived or numeric content into bar, line, pie, histogram, and Sankey charts on the canvas. Charts are useful when a branch contains data you want to explain visually."
          },
          {
            "action": "Document View",
            "description": "Long chat content can be opened in the document viewer for easier reading and review. This is especially helpful for lengthy drafts, specifications, or dense research summaries."
          },
          {
            "action": "HTML Preview",
            "description": "The HTML Renderer node can take markup and show a live preview inside the graph, with an optional pop-out preview window for closer inspection."
          },
          {
            "action": "Node Context Menus",
            "description": "Right-click chat, code, and plugin nodes for copy, export, document view, branch isolation, regeneration, takeaways, explainers, charts, image generation, and deletion actions. When multiple chat nodes are selected, the menu also exposes group summary workflows."
          }
        ]
      }
    ]
  },
  {
    "name": "Navigation",
    "description": "Mouse, keyboard, search, command palette, and view controls for moving quickly through small or very large graphs.",
    "subsections": [
      {
        "title": "Mouse and View Controls",
        "items": [
          {
            "action": "Pan the Canvas",
            "description": "Hold the Middle Mouse Button and drag to move around the workspace. This is the fastest way to traverse large graphs once you stop thinking of the canvas like a normal chat window."
          },
          {
            "action": "Zoom and Focus",
            "description": "Use Ctrl + Mouse Wheel to zoom, or use the toolbar buttons when you want a fixed step. Q and E also zoom out and in from the keyboard."
          },
          {
            "action": "Zoom to an Area",
            "description": "Hold Shift and drag a rectangle to zoom the view to a specific region. It is a precise way to jump into a dense subsection of a large project."
          },
          {
            "action": "Fit All and Reset",
            "description": "Fit All reframes the canvas around everything currently on it, while Reset restores the default zoom level. These two actions are the quickest recovery tools when you get lost."
          },
          {
            "action": "Minimap and Overlays",
            "description": "Use the Controls toggle to reveal drag, grid, and font tools, and use the minimap to jump directly to distant nodes. This becomes more valuable as your project spreads out."
          }
        ]
      },
      {
        "title": "Keyboard Workflow",
        "items": [
          {
            "action": "WASD and Branch Navigation",
            "description": "W, A, S, and D pan the view, while Ctrl + Arrow Keys move between parent, child, and sibling nodes in the current branch. This makes branch review much faster than constant mouse travel."
          },
          {
            "action": "Command Palette",
            "description": "Press Ctrl + K to open a searchable command palette for layout, note creation, selection, navigation, plugin insertion, chart generation, and more. It is the fastest way to discover power-user actions."
          },
          {
            "action": "Canvas Search",
            "description": "Press Ctrl + F to search across chat, code, document, image, thinking, and plugin nodes. Search results can be stepped through so you can quickly revisit important content."
          },
          {
            "action": "Selection Utilities",
            "description": "Delete removes the current selection, and the command palette also exposes focus selection, select all, collapse all, and expand all. These commands are useful once a graph becomes visually dense."
          }
        ]
      },
      {
        "title": "Shortcut Reference",
        "items": [
          {
            "action": "Ctrl + T / Ctrl + L / Ctrl + S",
            "description": "Start a new chat, open the Library, or save the current project. These are the main project-level shortcuts you will use most often."
          },
          {
            "action": "Ctrl + G / Ctrl + Shift + G / Ctrl + N",
            "description": "Wrap the current selection in a Frame, create a Container, or add a new Note. These three actions cover most day-to-day organization work."
          },
          {
            "action": "Ctrl + Left-Click / Ctrl + Right-Click",
            "description": "Add a connection pin to reroute a line, or remove a pin that you no longer need. Connection pins are helpful when you want to untangle overlapping paths."
          }
        ]
      }
    ]
  },
  {
    "name": "Organization",
    "description": "Ways to group, label, tidy, and visually manage complex canvases as a project grows.",
    "subsections": [
      {
        "title": "Structuring Large Graphs",
        "items": [
          {
            "action": "Frames",
            "description": "Select node-like items and press Ctrl + G to wrap them in a frame. Frames are best for labeling a visual section of the project without forcing the grouped items to move as one rigid unit."
          },
          {
            "action": "Containers",
            "description": "Use Ctrl + Shift + G to create a container when a set of items should move together. Containers are useful for keeping a working cluster intact while you reorganize the rest of the canvas."
          },
          {
            "action": "Titles and Colors",
            "description": "Frames and containers can be renamed and recolored so sections of the graph read like chapters or workstreams. This helps when you want the canvas to double as a presentation surface."
          },
          {
            "action": "Auto-Organize Tree Layout",
            "description": "The Organize button arranges conversational and plugin branches into a cleaner horizontal tree. It is a fast way to recover readability after a burst of exploratory work."
          }
        ]
      },
      {
        "title": "Orientation Aids",
        "items": [
          {
            "action": "Navigation Pins",
            "description": "Pins mark important places on the canvas and are saved with the chat. Use the Pins toolbar button to reveal the overlay and jump back to major milestones or hotspots."
          },
          {
            "action": "Connection Pins",
            "description": "Ctrl + left-click a connection to add a routing pin and shape the line, then Ctrl + right-click a pin to remove it. This is especially helpful when several branches overlap visually."
          },
          {
            "action": "Grid and Guide Controls",
            "description": "The controls overlay can enable snap-to-grid, smart guides, orthogonal routing, font controls, and faded connections. These tools are useful when a canvas needs visual cleanup rather than new AI output."
          },
          {
            "action": "Reduce Visual Noise",
            "description": "Combine collapse, grouping, branch isolation, and layout tools to keep the graph readable. Graphlink works best when you treat organization as part of the thinking process, not just post-processing."
          }
        ]
      }
    ]
  },
  {
    "name": "Plugins & Tools",
    "description": "Specialist nodes that extend a branch into research, reasoning, coding, drafting, rendering, and comparison workflows.",
    "subsections": [
      {
        "title": "Research and Analysis",
        "items": [
          {
            "action": "Graphlink-Web",
            "description": "Use this when a branch depends on current information or external sources. The node runs a web retrieval flow, summarizes the findings, and stores source links directly in the result."
          },
          {
            "action": "Conversation Node",
            "description": "Creates a self-contained linear chat surface inside the graph. Use it when you want a focused sub-conversation that can be pruned and iterated without expanding the main branch too aggressively."
          }
        ]
      },
      {
        "title": "Build, Draft, and Render",
        "items": [
          {
            "action": "System Prompt",
            "description": "Adds a branch-scoped system prompt note that changes assistant behavior only for that conversation path. This is ideal when you want a role, tone, or instruction change without affecting the rest of the project."
          },
          {
            "action": "Py-Coder",
            "description": "A coding workspace with AI-driven and manual modes, generated code, terminal output, and final analysis tabs. Use it for fast implementation, debugging, code generation, and lightweight computation."
          },
          {
            "action": "Execution Sandbox",
            "description": "Runs Python in a per-node virtualenv with declared dependencies - the venv isolates installed packages, not the operating system; code still runs with your full account privileges. Choose this over Py-Coder when you need dependency-aware execution or a cleaner reproducible runtime."
          },
          {
            "action": "Artifact / Drafter",
            "description": "A living markdown drafting surface for reports, specs, briefs, and other documents that need repeated revision. It is the most natural place to keep polished long-form output inside the graph."
          },
          {
            "action": "HTML Renderer",
            "description": "Turns HTML or UI markup into a preview pane inside the graph and can pop the preview into a separate window. It is the right tool when a branch needs visual feedback instead of plain text output."
          }
        ]
      }
    ]
  },
  {
    "name": "Settings & Models",
    "description": "How runtime modes, providers, model routing, and quality-of-life settings shape the behavior of the application.",
    "subsections": [
      {
        "title": "Runtime Modes",
        "items": [
          {
            "action": "Ollama (Local)",
            "description": "Use local Ollama when you want on-device chat and reasoning. You can choose a default chat model and switch between Quick mode and Thinking mode for different response styles."
          },
          {
            "action": "API Endpoint Mode",
            "description": "Use API Endpoint mode for OpenAI-compatible providers or Gemini. This unlocks hosted models, per-task model routing, and the image-generation path used by the canvas."
          },
          {
            "action": "Per-Task Model Selection",
            "description": "Graphlink can store different models for title generation, main chat, chart generation, image generation, web validation, and web summarization. This lets you optimize cost and quality across different tools."
          },
          {
            "action": "Live Provider Reconfiguration",
            "description": "Switching modes reinitializes the active provider for the current session. If a provider is missing credentials or cannot initialize, the app warns you before you keep working."
          }
        ]
      },
      {
        "title": "Personalization and Feedback",
        "items": [
          {
            "action": "Appearance",
            "description": "Theme and appearance settings restyle the app, including flyouts and node accents, around the current palette. This is mainly cosmetic, but it helps tailor the workspace to your preference."
          },
          {
            "action": "Token Counter",
            "description": "When enabled, the token counter shows prompt, context, output, and running session totals. It is useful when you want a sense of branch size or model budget."
          },
          {
            "action": "Provider-Specific Guidance",
            "description": "API settings explain which providers are supported and which fields matter for each one. OpenAI-compatible endpoints and Gemini use slightly different setup and model-loading paths."
          },
          {
            "action": "Optional Feature Dependencies",
            "description": "Some features rely on optional libraries, such as PDF or DOCX import-export support and HTML preview support. When something is unavailable, Graphlink tries to tell you what dependency is missing."
          }
        ]
      }
    ]
  },
  {
    "name": "Saving & Output",
    "description": "Persistence, attachments, exports, and the ways Graphlink turns branch results into reusable project deliverables.",
    "subsections": [
      {
        "title": "Persistence and Library",
        "items": [
          {
            "action": "Background Saves",
            "description": "Use Save to persist the current graph, including chats, plugin nodes, notes, pins, frames, containers, and view state. This makes it practical to treat a chat like a reusable workspace instead of a disposable session."
          },
          {
            "action": "Chat Library",
            "description": "Open the Library with the toolbar or Ctrl + L to reopen, rename, organize, or continue past projects. The Library is the main entry point for resuming work across sessions."
          },
          {
            "action": "Local Project Record",
            "description": "Because the graph is saved as a full project state, your canvas can function as a working notebook, task map, and decision trail all at once."
          }
        ]
      },
      {
        "title": "Attachments and Exports",
        "items": [
          {
            "action": "Supported Attachments",
            "description": "You can attach common text and code files, JSON, CSV, XML, HTML, CSS, JS, and Markdown. PDF and DOCX attachments are also supported when their reader libraries are installed."
          },
          {
            "action": "Drag and Drop",
            "description": "Files can be staged by dragging them onto the canvas or by using the paperclip button. The attachment becomes a visible branch node so it is clear what source material was provided."
          },
          {
            "action": "Export Formats",
            "description": "Chat, document, and code content can be exported in practical formats such as TXT, Markdown, HTML, and PDF. Chat and document content also support DOCX, and code can be exported as a Python script."
          },
          {
            "action": "Generated Outputs",
            "description": "Image generation, chart creation, takeaway notes, explainers, group summaries, and branch diff notes all let you promote transient AI output into reusable project artifacts."
          }
        ]
      }
    ]
  },
  {
    "name": "Use Cases",
    "description": "Practical ways to use the full application beyond simple back-and-forth prompting.",
    "subsections": [
      {
        "title": "Common Workflows",
        "items": [
          {
            "action": "Research and Comparison",
            "description": "Start with a broad question, branch different answers, use Graphlink-Web to ground facts, and finish with Branch Lens when you want a disciplined comparison between competing paths."
          },
          {
            "action": "Coding and Debugging",
            "description": "Ask for code on the main canvas, move promising results into Py-Coder for iteration, and switch to Execution Sandbox when dependencies or reproducibility matter. This keeps ideation and execution connected."
          },
          {
            "action": "Planning and Execution",
            "description": "Use Workflow Architect when a goal feels large or undefined. It can convert a vague request into a clearer plugin sequence so your next few steps are already framed."
          },
          {
            "action": "Shipping and Hardening",
            "description": "When a branch is close to done, run Quality Gate to judge whether it actually meets the bar. It is especially useful before handoff, release, demos, or any moment when confidence matters more than momentum."
          },
          {
            "action": "Long-Form Writing",
            "description": "Keep source research nearby, use reasoning where needed, and let Artifact / Drafter hold the polished document. This is a strong workflow for specs, reports, proposals, and internal documentation."
          }
        ]
      },
      {
        "title": "Knowledge Work Patterns",
        "items": [
          {
            "action": "Meeting and Study Notes",
            "description": "Generate takeaways, explainer notes, and group summaries around important nodes so the final canvas reads like a structured knowledge map rather than a raw transcript dump."
          },
          {
            "action": "Product and UX Exploration",
            "description": "Branch alternative product directions, draft requirements, render HTML prototypes, and compare variants without losing the reasoning that led to each option."
          },
          {
            "action": "Data Storytelling",
            "description": "Turn a branch that contains numbers or structure into charts, then keep the explanation, source inputs, and conclusions beside the visualization on the same canvas."
          },
          {
            "action": "Prompt Variation by Branch",
            "description": "Use System Prompt nodes and branching to test different assistant behaviors against the same underlying project. This is especially useful when you want to compare tone, role, or working style."
          }
        ]
      }
    ]
  }
];
