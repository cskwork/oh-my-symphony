// Plain-language button guides, one per PM area (spec D7). Labels match the
// rendered button text exactly; Korean text lives in the index.js KO map.
export const PM_GUIDES = {
  workspace: {
    'New change': 'Creates a new planning change in a separate Git working folder (worktree) that you name. Other changes stay as they are.',
    'Repair workspace': 'Reconnects this change to its planning folder after the folder was moved or lost, then reads its documents again.',
    'Convert legacy plan': 'Replaces an older board ticket with a new planning change: the ticket is parked, and only tasks approved here can carry out its work.',
    'Edit in external editor': 'Pauses editing on this page so you can change the planning files in another editor; finishing reads and validates them again.',
    'Validate': 'Checks every planning document against the rules and OpenSpec and lists what blocks approval. Changes nothing.',
    'Approve revision': 'Runs the same checks as Validate and records your sign-off on this exact version of the plan. Any later edit needs a new approval before handoff.',
  },
  idea: {
    'Edit source': 'Opens the plain text of this document for editing; nothing is saved until you press Save draft.',
    'Save draft': 'Saves your edits as a new revision of the plan. It does not approve or hand off anything.',
    'Planning assistant': 'Asks an AI assistant to suggest changes to the plan; you review the proposal, and nothing changes unless you apply it.',
    'Add decision': 'Adds a decision with the title you type, with room for its owner, impact, status and reason.',
  },
  requirements: {
    'Add capability': 'Starts a requirements document for a new capability (an area of the product) with a first requirement that you name.',
    'Edit requirements': 'Opens the requirements of this capability for editing; nothing is saved until you press Save draft.',
    'Add requirement': 'Adds a requirement with the title you type; each requirement states something the product must do.',
    'Add scenario': 'Adds a scenario with the title you type: a concrete example that shows a requirement working.',
    'Waive coverage': 'Records a reviewed reason why a requirement does not need full coverage. It does not mark any work as done or accepted.',
    'Revoke waiver': 'Withdraws an active waiver, so the requirement needs normal coverage again.',
  },
  design: {
    'Storyboard': 'Shows each screen as a frame: what the user sees and does, and how the system responds.',
    'Save storyboard': 'Saves your edits to the open storyboard frame as a new revision of the plan.',
    'Add storyboard': 'Adds a storyboard frame with the title you type to the UX document.',
    'Add contract': 'Adds an API contract with the title you type, describing who owns it, what data it exchanges and how errors are handled.',
  },
  plan: {
    'Work breakdown': 'Shows the plan as a tree of groups and tasks with owner, priority and estimate.',
    'Dependencies': 'Lists which tasks must wait for other tasks, and why.',
    'Add group': 'Adds a group that holds related tasks; a group itself is never handed off as work.',
    'Add child': 'Adds a task under the selected group; with no selection, adds a root task. Executable tasks cannot contain children.',
    'Delete': 'Removes the selected task while the plan is not yet approved; Undo brings it back.',
    'Cancel task': 'Takes the place of Delete after approval: cancels the selected task with a reason you give and keeps its reviewed scope on record.',
    'Undo': 'Reverses your last change to the work breakdown.',
    'Download export': 'Downloads the plan as an Excel workbook, CSV, Markdown, printable HTML or a planning package. Changes nothing.',
    'Preview import': 'Reads an edited Excel workbook and lists each proposed change and conflict; nothing is applied until you review and apply it.',
    'Preview approved handoff': 'Shows the board tickets the approved plan would create; nothing is created until you confirm.',
    'Preview archive': 'Checks that all work is accepted and shows what archiving would record; nothing is archived until you confirm.',
  },
  code: {
    'Run verification': 'Has this computer run an approved check on the code of a task and records the actual result.',
    'Submit completion evidence': 'Records the result of a check that a person ran, with its report. It does not complete or accept the task.',
    'Review Git commit': 'Opens the code change (Git commit) that this task produced.',
    'Open run evidence': 'Opens the record of the agent run that worked on this task.',
    'Accept verified revision': 'Signs off the work of a task once all its required checks have passed; nothing is accepted automatically.',
  },
};
