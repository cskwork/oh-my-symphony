# Leave request journeys

## Actors and journeys
An employee submits leave dates. Their team manager decides the request. Teammates check approved leave before planning work.

## Screen inventory
- /leave/new: Leave request form.
- /leave/inbox: Manager's Pending requests.
- /team/calendar: Approved leave for the signed-in user's team.

## Loading, empty, error, and permission states
Show loading while saving or fetching. An empty inbox says there are no Pending requests; an empty calendar says no approved leave. Keep entered dates or the decline reason after an error. Ask signed-out users to sign in and refuse manager actions outside their team.

## Responsive and accessibility behavior
Stack controls on narrow screens. Label the date fields and action buttons, move focus to validation errors, and announce saved status. Calendar entries include names and dates in text, so color is not the only cue.

## Storyboard

### Storyboard: Employee submits a request
<!-- symphony:storyboard={{ID:request_form}} -->
- Screen/route: /leave/new
- Actor/goal: Employee requests two days of leave.
- Initial state: Signed in; no reserved dates for the selected range.
- UI: Start date, end date, Submit button, and status message.
- User action: Select 2026-11-02 through 2026-11-03 and press Submit.
- System response: Save the request as Pending and show its dates and status.
- Success: The employee sees confirmation and knows the manager must decide.
- Alternate: Change the dates before submitting; no request is stored yet.
- Failure: Overlap or invalid dates show a field error; a save failure keeps the entered dates and allows retry.
- Requirement references: {{ID:submit_request}}, {{ID:request_pending}}, {{ID:overlap_refused}}
- Design references: {{ID:submit_leave}}

### Storyboard: Manager decides in the inbox
<!-- symphony:storyboard={{ID:manager_inbox}} -->
- Screen/route: /leave/inbox
- Actor/goal: Manager reviews a request from their own team.
- Initial state: A team employee has a Pending request.
- UI: Employee name, dates, Approve button, Decline button, and decline reason field.
- User action: Review the dates and press Approve.
- System response: Save Approved status and notify the employee.
- Success: The request leaves the Pending list and its decision is confirmed.
- Alternate: Enter a reason and press Decline; the request becomes Declined.
- Failure: A missing decline reason leaves the request Pending. A request already decided or outside the manager's team is refused and the list refreshes.
- Requirement references: {{ID:decide_request}}, {{ID:approval_notifies}}, {{ID:decline_reason_required}}
- Design references: {{ID:decide_leave}}

### Storyboard: Team checks the calendar
<!-- symphony:storyboard={{ID:team_calendar}} -->
- Screen/route: /team/calendar
- Actor/goal: Teammate sees who will be away before planning work.
- Initial state: A request for the user's team has been approved.
- UI: Month navigation (reusing the request form's date picker) and approved leave entries with employee names and dates.
- User action: Open November 2026.
- System response: Load only the team's Approved leave and place it on every included date.
- Success: The teammate sees approved absences; Pending and Declined requests stay hidden.
- Alternate: A month without approved leave shows an empty message.
- Failure: A load failure shows Retry and does not present stale data as current.
- Requirement references: {{ID:approved_calendar}}, {{ID:calendar_approved_only}}
- Design references: {{ID:decide_leave}}
