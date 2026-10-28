# Leave requests

## ADDED Requirements

### Requirement: Submit a leave request
<!-- symphony:req={{ID:submit_request}} -->

The system SHALL store a valid leave request as Pending and MUST refuse dates that overlap the same employee's Pending or Approved requests. The start date MUST be on or before the end date; both dates are included.

#### Scenario: Valid request becomes Pending
<!-- symphony:scenario={{ID:request_pending}} -->
- **GIVEN** a signed-in employee with no reserved dates from 2026-11-02 through 2026-11-03
- **WHEN** the employee submits that date range
- **THEN** the system stores one Pending request and shows its dates and status

#### Scenario: Overlapping dates are refused
<!-- symphony:scenario={{ID:overlap_refused}} -->
- **GIVEN** the employee has a Pending request from 2026-11-02 through 2026-11-03
- **WHEN** the employee submits another request from 2026-11-03 through 2026-11-04
- **THEN** the system refuses the overlapping request, stores no second request, and asks the employee to change the dates

### Requirement: Approve or decline a request
<!-- symphony:req={{ID:decide_request}} -->

The system SHALL let a manager approve or decline a Pending request only for their own team. Approval MUST change the status to Approved and notify the employee. A decline MUST include a non-empty reason and change the status to Declined.

#### Scenario: Approval notifies the employee
<!-- symphony:scenario={{ID:approval_notifies}} -->
- **GIVEN** a Pending request belonging to an employee on the manager's team
- **WHEN** the manager approves it
- **THEN** the request becomes Approved and the employee receives an approval notification

#### Scenario: Decline requires a reason
<!-- symphony:scenario={{ID:decline_reason_required}} -->
- **GIVEN** a Pending request belonging to an employee on the manager's team
- **WHEN** the manager tries to decline it without a reason
- **THEN** the request remains Pending and the inbox asks for a reason before saving the decline
