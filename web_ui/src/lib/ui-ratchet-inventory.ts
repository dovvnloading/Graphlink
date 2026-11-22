/**
 * UI-02 baseline from main (626e10567a82c9aaf9b33e3bcbd75d708463537b).
 * Keep one entry per rule/file, sorted by relative path, and leave a blank
 * line between entries to minimize conflicts with parallel UI migrations.
 * Zero-count files are intentionally absent: an unpinned file must stay at 0.
 *
 * app/styles.css pins for R9–R12 and R14 are maxima rather than exact counts,
 * to avoid blocking unrelated ongoing CSS cleanups. All other pins are exact.
 * See docs/DESIGN_SYSTEM.md for the replacement component conventions.
 */
export type RatchetRule =
  | "R1" | "R2" | "R3" | "R4" | "R5" | "R6" | "R7"
  | "R8" | "R9" | "R10" | "R11" | "R12" | "R13" | "R14";

export const UI_RATCHET_INVENTORY: ReadonlyArray<{
  readonly file: string;
  readonly rule: RatchetRule;
  readonly count: number;
}> = [
  { file: "app/App.tsx", rule: "R2", count: 1 },

  { file: "app/App.tsx", rule: "R8", count: 1 },

  { file: "app/canvas/AgentWorkspaceBrowser.tsx", rule: "R1", count: 2 },

  { file: "app/canvas/AgentWorkspaceBrowser.tsx", rule: "R8", count: 3 },

  { file: "app/canvas/ArtifactNodeView.tsx", rule: "R7", count: 2 },

  { file: "app/canvas/ArtifactNodeView.tsx", rule: "R8", count: 1 },

  { file: "app/canvas/ChartNodeView.tsx", rule: "R1", count: 1 },

  { file: "app/canvas/ChartNodeView.tsx", rule: "R4", count: 3 },

  { file: "app/canvas/ChartNodeView.tsx", rule: "R6", count: 2 },

  { file: "app/canvas/ChartNodeView.tsx", rule: "R8", count: 1 },

  { file: "app/canvas/ChatCitationChips.css", rule: "R9", count: 5 },

  { file: "app/canvas/ChatCitationChips.css", rule: "R10", count: 1 },

  { file: "app/canvas/ChatCitationChips.css", rule: "R14", count: 13 },

  { file: "app/canvas/ChatNodeView.tsx", rule: "R1", count: 7 },

  { file: "app/canvas/ChatNodeView.tsx", rule: "R2", count: 2 },

  { file: "app/canvas/ChatNodeView.tsx", rule: "R5", count: 1 },

  { file: "app/canvas/ChatNodeView.tsx", rule: "R7", count: 26 },

  { file: "app/canvas/ChatNodeView.tsx", rule: "R8", count: 16 },

  { file: "app/canvas/ChatRerunDialog.css", rule: "R9", count: 2 },

  { file: "app/canvas/ChatRerunDialog.css", rule: "R10", count: 4 },

  { file: "app/canvas/ChatRerunDialog.tsx", rule: "R6", count: 3 },

  { file: "app/canvas/ChatRerunDialog.tsx", rule: "R8", count: 1 },

  { file: "app/canvas/ChatScienceCard.tsx", rule: "R5", count: 1 },

  { file: "app/canvas/ChatScienceCard.tsx", rule: "R8", count: 2 },

  { file: "app/canvas/ChatVersionCompareDialog.tsx", rule: "R4", count: 2 },

  { file: "app/canvas/ChatVersionCompareDialog.tsx", rule: "R8", count: 1 },

  { file: "app/canvas/CodeExecutionApprovalPanel.tsx", rule: "R6", count: 1 },

  { file: "app/canvas/CodeNodeView.tsx", rule: "R7", count: 5 },

  { file: "app/canvas/CodeReviewNodeView.tsx", rule: "R7", count: 2 },

  { file: "app/canvas/CodeReviewNodeView.tsx", rule: "R8", count: 6 },

  { file: "app/canvas/CodeSandboxNodeView.tsx", rule: "R5", count: 1 },

  { file: "app/canvas/CodeSandboxNodeView.tsx", rule: "R7", count: 2 },

  { file: "app/canvas/CodeSandboxNodeView.tsx", rule: "R8", count: 2 },

  { file: "app/canvas/CollapseToggleButton.tsx", rule: "R2", count: 1 },

  { file: "app/canvas/CollapseToggleButton.tsx", rule: "R8", count: 1 },

  { file: "app/canvas/ConversationNodeView.tsx", rule: "R2", count: 1 },

  { file: "app/canvas/ConversationNodeView.tsx", rule: "R7", count: 5 },

  { file: "app/canvas/ConversationNodeView.tsx", rule: "R8", count: 4 },

  { file: "app/canvas/DocumentNodeView.tsx", rule: "R7", count: 7 },

  { file: "app/canvas/DocumentNodeView.tsx", rule: "R8", count: 2 },

  { file: "app/canvas/DocumentViewMarkdown.tsx", rule: "R8", count: 1 },

  { file: "app/canvas/DocumentViewPanel.tsx", rule: "R8", count: 6 },

  { file: "app/canvas/DocumentViewSearch.tsx", rule: "R1", count: 3 },

  { file: "app/canvas/DocumentViewSearch.tsx", rule: "R8", count: 3 },

  { file: "app/canvas/DocumentViewToc.tsx", rule: "R7", count: 1 },

  { file: "app/canvas/DocumentViewToc.tsx", rule: "R8", count: 1 },

  { file: "app/canvas/FalMediaNodeView.css", rule: "R9", count: 65 },

  { file: "app/canvas/FalMediaNodeView.css", rule: "R10", count: 28 },

  { file: "app/canvas/FalMediaNodeView.css", rule: "R14", count: 132 },

  { file: "app/canvas/FalMediaNodeView.tsx", rule: "R1", count: 23 },

  { file: "app/canvas/FalMediaNodeView.tsx", rule: "R3", count: 3 },

  { file: "app/canvas/FalMediaNodeView.tsx", rule: "R4", count: 2 },

  { file: "app/canvas/FalMediaNodeView.tsx", rule: "R5", count: 2 },

  { file: "app/canvas/FalMediaNodeView.tsx", rule: "R6", count: 1 },

  { file: "app/canvas/FalMediaNodeView.tsx", rule: "R8", count: 6 },

  { file: "app/canvas/FalMediaUploader.tsx", rule: "R1", count: 1 },

  { file: "app/canvas/FalMediaUploader.tsx", rule: "R3", count: 1 },

  { file: "app/canvas/GitlinkNodeView.tsx", rule: "R6", count: 2 },

  { file: "app/canvas/GitlinkNodeView.tsx", rule: "R7", count: 2 },

  { file: "app/canvas/GitlinkNodeView.tsx", rule: "R8", count: 2 },

  { file: "app/canvas/GroupColorPicker.tsx", rule: "R7", count: 4 },

  { file: "app/canvas/GroupColorPicker.tsx", rule: "R8", count: 3 },

  { file: "app/canvas/GroupNodeView.tsx", rule: "R2", count: 1 },

  { file: "app/canvas/GroupNodeView.tsx", rule: "R8", count: 5 },

  { file: "app/canvas/HarnessNodeView.tsx", rule: "R1", count: 6 },

  { file: "app/canvas/HarnessNodeView.tsx", rule: "R2", count: 1 },

  { file: "app/canvas/HarnessNodeView.tsx", rule: "R4", count: 4 },

  { file: "app/canvas/HarnessNodeView.tsx", rule: "R5", count: 1 },

  { file: "app/canvas/HarnessNodeView.tsx", rule: "R6", count: 2 },

  { file: "app/canvas/HarnessNodeView.tsx", rule: "R7", count: 2 },

  { file: "app/canvas/HarnessNodeView.tsx", rule: "R8", count: 9 },

  { file: "app/canvas/HtmlNodeView.tsx", rule: "R8", count: 1 },

  { file: "app/canvas/ImageNodeView.tsx", rule: "R7", count: 5 },

  { file: "app/canvas/NodeCards.css", rule: "R9", count: 14 },

  { file: "app/canvas/NodeCards.css", rule: "R10", count: 24 },

  { file: "app/canvas/NodeCards.css", rule: "R14", count: 87 },

  { file: "app/canvas/NodeHeading.tsx", rule: "R2", count: 1 },

  { file: "app/canvas/NodeMarkdown.tsx", rule: "R8", count: 3 },

  { file: "app/canvas/NodeShell.tsx", rule: "R2", count: 1 },

  { file: "app/canvas/NodeShell.tsx", rule: "R8", count: 6 },

  { file: "app/canvas/NoteNodeView.tsx", rule: "R1", count: 4 },

  { file: "app/canvas/NoteNodeView.tsx", rule: "R8", count: 3 },

  { file: "app/canvas/NoteTextInput.tsx", rule: "R4", count: 2 },

  { file: "app/canvas/NoteTextInput.tsx", rule: "R8", count: 1 },

  { file: "app/canvas/PlanNodeView.tsx", rule: "R2", count: 6 },

  { file: "app/canvas/PlanNodeView.tsx", rule: "R5", count: 1 },

  { file: "app/canvas/PlanNodeView.tsx", rule: "R7", count: 2 },

  { file: "app/canvas/PlanNodeView.tsx", rule: "R8", count: 1 },

  { file: "app/canvas/SceneCanvas.tsx", rule: "R2", count: 1 },

  { file: "app/canvas/SceneCanvas.tsx", rule: "R8", count: 2 },

  { file: "app/canvas/SceneCanvas.tsx", rule: "R13", count: 1 },

  { file: "app/canvas/SceneMinimap.css", rule: "R10", count: 2 },

  { file: "app/canvas/SceneMinimap.css", rule: "R14", count: 3 },

  { file: "app/canvas/SceneMinimap.tsx", rule: "R2", count: 1 },

  { file: "app/canvas/SceneMinimap.tsx", rule: "R8", count: 3 },

  { file: "app/canvas/ScienceArrayInput.tsx", rule: "R1", count: 1 },

  { file: "app/canvas/ScienceArrayInput.tsx", rule: "R5", count: 1 },

  { file: "app/canvas/ScienceColumnBinding.tsx", rule: "R4", count: 3 },

  { file: "app/canvas/ScienceColumnBinding.tsx", rule: "R5", count: 1 },

  { file: "app/canvas/ScienceTableSource.tsx", rule: "R4", count: 1 },

  { file: "app/canvas/ScientificDataTable.tsx", rule: "R5", count: 1 },

  { file: "app/canvas/ScientificNodeView.css", rule: "R9", count: 18 },

  { file: "app/canvas/ScientificNodeView.css", rule: "R10", count: 5 },

  { file: "app/canvas/ScientificNodeView.css", rule: "R14", count: 66 },

  { file: "app/canvas/ScientificNodeView.tsx", rule: "R2", count: 1 },

  { file: "app/canvas/ScientificNodeView.tsx", rule: "R4", count: 9 },

  { file: "app/canvas/ScientificNodeView.tsx", rule: "R5", count: 7 },

  { file: "app/canvas/ScientificNodeView.tsx", rule: "R6", count: 1 },

  { file: "app/canvas/ScientificNodeView.tsx", rule: "R8", count: 8 },

  { file: "app/canvas/ScientificResultViews.css", rule: "R9", count: 6 },

  { file: "app/canvas/ScientificResultViews.css", rule: "R10", count: 3 },

  { file: "app/canvas/ScientificResultViews.css", rule: "R14", count: 12 },

  { file: "app/canvas/ScientificResultViews.tsx", rule: "R1", count: 1 },

  { file: "app/canvas/ScientificResultViews.tsx", rule: "R5", count: 2 },

  { file: "app/canvas/ScientificResultViews.tsx", rule: "R8", count: 3 },

  { file: "app/canvas/ScientificWorkspace.tsx", rule: "R8", count: 1 },

  { file: "app/canvas/StalenessBadge.tsx", rule: "R2", count: 1 },

  { file: "app/canvas/StalenessBadge.tsx", rule: "R8", count: 1 },

  { file: "app/canvas/TableContributors.tsx", rule: "R4", count: 1 },

  { file: "app/canvas/TableSource.tsx", rule: "R4", count: 2 },

  { file: "app/canvas/TableWorkflow.css", rule: "R9", count: 1 },

  { file: "app/canvas/TableWorkflow.css", rule: "R10", count: 1 },

  { file: "app/canvas/TableWorkflow.css", rule: "R14", count: 17 },

  { file: "app/canvas/ThinkingNodeView.tsx", rule: "R7", count: 4 },

  { file: "app/canvas/ViewportDock.tsx", rule: "R2", count: 2 },

  { file: "app/canvas/ViewportDock.tsx", rule: "R8", count: 4 },

  { file: "app/canvas/WebResearchNodeView.tsx", rule: "R6", count: 1 },

  { file: "app/canvas/WebResearchNodeView.tsx", rule: "R7", count: 2 },

  { file: "app/canvas/WebResearchNodeView.tsx", rule: "R8", count: 1 },

  { file: "app/canvas/workspace.css", rule: "R9", count: 6 },

  { file: "app/canvas/workspace.css", rule: "R10", count: 8 },

  { file: "app/canvas/workspace.css", rule: "R11", count: 2 },

  { file: "app/canvas/workspace.css", rule: "R14", count: 31 },

  { file: "app/chrome/AboutDialog.tsx", rule: "R8", count: 1 },

  { file: "app/chrome/AppBar.tsx", rule: "R8", count: 3 },

  { file: "app/chrome/AppBarIcon.tsx", rule: "R2", count: 18 },

  { file: "app/chrome/BuilderLaunchDialog.tsx", rule: "R5", count: 1 },

  { file: "app/chrome/BuilderLaunchDialog.tsx", rule: "R6", count: 2 },

  { file: "app/chrome/BuilderLaunchDialog.tsx", rule: "R8", count: 1 },

  { file: "app/chrome/ChatLibraryDialog.tsx", rule: "R2", count: 10 },

  { file: "app/chrome/ChatLibraryDialog.tsx", rule: "R3", count: 3 },

  { file: "app/chrome/ChatLibraryDialog.tsx", rule: "R8", count: 2 },

  { file: "app/chrome/ChatLibraryDialog.tsx", rule: "R13", count: 1 },

  { file: "app/chrome/CloudProviderSettings.css", rule: "R9", count: 11 },

  { file: "app/chrome/CloudProviderSettings.css", rule: "R10", count: 3 },

  { file: "app/chrome/CloudProviderSettings.css", rule: "R14", count: 41 },

  { file: "app/chrome/CloudProviderSettings.tsx", rule: "R2", count: 1 },

  { file: "app/chrome/Composer.tsx", rule: "R1", count: 3 },

  { file: "app/chrome/Composer.tsx", rule: "R2", count: 1 },

  { file: "app/chrome/Composer.tsx", rule: "R8", count: 9 },

  { file: "app/chrome/CustomSelect.tsx", rule: "R2", count: 1 },

  { file: "app/chrome/CustomSelect.tsx", rule: "R13", count: 1 },

  { file: "app/chrome/DiagnosticsDialog.tsx", rule: "R8", count: 1 },

  { file: "app/chrome/GlobalSearchDialog.tsx", rule: "R8", count: 1 },

  { file: "app/chrome/GraphRunnerDialog.css", rule: "R9", count: 3 },

  { file: "app/chrome/GraphRunnerDialog.css", rule: "R10", count: 1 },

  { file: "app/chrome/GraphRunnerDialog.css", rule: "R14", count: 26 },

  { file: "app/chrome/GraphRunnerDialog.tsx", rule: "R4", count: 4 },

  { file: "app/chrome/GraphRunnerDialog.tsx", rule: "R8", count: 1 },

  { file: "app/chrome/HarnessLaunchDialog.tsx", rule: "R8", count: 2 },

  { file: "app/chrome/HarnessLaunchOptions.tsx", rule: "R4", count: 1 },

  { file: "app/chrome/HelpDialog.tsx", rule: "R8", count: 1 },

  { file: "app/chrome/KnowledgeSearchDialog.tsx", rule: "R8", count: 7 },

  { file: "app/chrome/KnowledgeSettingsPage.tsx", rule: "R4", count: 1 },

  { file: "app/chrome/KnowledgeSettingsPage.tsx", rule: "R6", count: 1 },

  { file: "app/chrome/KnowledgeSettingsPage.tsx", rule: "R13", count: 3 },

  { file: "app/chrome/ModelComparisonPicker.tsx", rule: "R1", count: 1 },

  { file: "app/chrome/ModelPicker.tsx", rule: "R1", count: 1 },

  { file: "app/chrome/ModelPicker.tsx", rule: "R8", count: 1 },

  { file: "app/chrome/ModelPricingSettings.css", rule: "R9", count: 1 },

  { file: "app/chrome/ModelPricingSettings.css", rule: "R14", count: 6 },

  { file: "app/chrome/NotificationBanner.tsx", rule: "R1", count: 1 },

  { file: "app/chrome/OllamaSettingsPage.tsx", rule: "R6", count: 1 },

  { file: "app/chrome/OllamaSettingsPage.tsx", rule: "R8", count: 2 },

  { file: "app/chrome/OllamaSettingsPage.tsx", rule: "R13", count: 5 },

  { file: "app/chrome/OnboardingDialog.tsx", rule: "R8", count: 1 },

  { file: "app/chrome/OnboardingSteps.tsx", rule: "R8", count: 1 },

  { file: "app/chrome/OrganizationDialog.css", rule: "R9", count: 10 },

  { file: "app/chrome/OrganizationDialog.css", rule: "R10", count: 4 },

  { file: "app/chrome/OrganizationDialog.css", rule: "R14", count: 23 },

  { file: "app/chrome/OrganizationDialog.tsx", rule: "R2", count: 1 },

  { file: "app/chrome/OrganizationDialog.tsx", rule: "R4", count: 1 },

  { file: "app/chrome/OrganizationDialog.tsx", rule: "R6", count: 3 },

  { file: "app/chrome/OrganizationDialog.tsx", rule: "R8", count: 2 },

  { file: "app/chrome/PinOverlay.tsx", rule: "R1", count: 2 },

  { file: "app/chrome/PinOverlay.tsx", rule: "R8", count: 1 },

  { file: "app/chrome/PluginPicker.tsx", rule: "R1", count: 1 },

  { file: "app/chrome/QuickSwitcherDialog.tsx", rule: "R3", count: 1 },

  { file: "app/chrome/RunnerDisclosure.tsx", rule: "R1", count: 2 },

  { file: "app/chrome/SearchIcon.tsx", rule: "R2", count: 1 },

  { file: "app/chrome/SearchOverlay.tsx", rule: "R1", count: 3 },

  { file: "app/chrome/SettingsDialog.tsx", rule: "R3", count: 4 },

  { file: "app/chrome/SettingsDialog.tsx", rule: "R4", count: 1 },

  { file: "app/chrome/SettingsDialog.tsx", rule: "R5", count: 4 },

  { file: "app/chrome/SettingsDialog.tsx", rule: "R6", count: 6 },

  { file: "app/chrome/SettingsDialog.tsx", rule: "R8", count: 2 },

  { file: "app/chrome/SettingsDialog.tsx", rule: "R13", count: 11 },

  { file: "app/chrome/ViewPopover.tsx", rule: "R6", count: 2 },

  { file: "app/chrome/ViewPopover.tsx", rule: "R8", count: 3 },

  { file: "app/context/ContextInspector.css", rule: "R9", count: 1 },

  { file: "app/context/ContextInspector.css", rule: "R10", count: 3 },

  { file: "app/context/ContextInspector.css", rule: "R14", count: 13 },

  { file: "app/context/ContextInspectorLauncher.tsx", rule: "R2", count: 1 },

  { file: "app/context/ContextInspectorLauncher.tsx", rule: "R8", count: 1 },

  { file: "app/overlays/overlays.tsx", rule: "R1", count: 1 },

  { file: "app/styles.css", rule: "R9", count: 392 },

  { file: "app/styles.css", rule: "R10", count: 217 },

  { file: "app/styles.css", rule: "R11", count: 3 },

  { file: "app/styles.css", rule: "R12", count: 10916 },

  { file: "app/styles.css", rule: "R14", count: 879 },

];

/** Each historical R1 exception must remain exact, too; it is not a blanket
 * file exclusion. These are the only non-icon uses of icon-like characters.
 * The diff signs are text in a comparison key, not replacement iconography.
 */
export const R1_ALLOWLIST = [
  {
    file: "app/canvas/ChatVersionCompareDialog.tsx",
    needle: "+",
    count: 1,
    reason: "Diff legend's insertion + is semantic diff notation, not a UI icon.",
  },

  {
    file: "app/canvas/ChatVersionCompareDialog.tsx",
    needle: "−",
    count: 1,
    reason: "Diff legend's deletion minus is semantic diff notation, not a UI icon.",
  },

  {
    file: "app/canvas/HarnessNodeView.tsx",
    needle: "×",
    count: 1,
    reason: "Compaction multiplier N× is a quantity suffix, not a close icon.",
  },

  {
    file: "app/canvas/scientificWorkbench.ts",
    needle: "×",
    count: 2,
    reason: "Scientific notation uses the multiplication sign in mantissa × 10^n.",
  },
] as const;
