# Team leave request

## Problem
Employees ask for leave in chat, and managers lose track of requests. Teammates cannot reliably see who will be away.

## Intended users
Employees submitting leave, managers reviewing their own team's requests, and teammates planning around approved leave.

## Desired outcome
An employee submits dates once, receives a clear decision, and sees approved leave on the team calendar.

## Scope
A leave request form, a manager inbox, and a team calendar. Store each request and its decision, refuse overlapping requests, and notify the employee after approval.

## Non-goals
Payroll, leave accrual, company holidays, and changes to annual leave balances. Showing remaining balance is a separate unresolved decision.

## Constraints
Use the existing employee sign-in and team membership. Dates are whole calendar days, including the start and end dates. Only a manager of the employee's team can decide a request.

## Success criteria
A valid request becomes Pending. An overlapping request is refused. Approval notifies the employee, a decline requires a reason, and only approved leave appears on the team calendar.

## Assumptions
Each employee belongs to one team with a manager. The existing notification channel can deliver approval messages. Pending and Approved requests reserve their dates; Declined requests do not.

## Open decisions

### Decision: Managers approve their own team's requests
<!-- symphony:decision={{ID:manager_scope}} -->
- Owner: Product manager
- Impact: Medium
- Status: Accepted
- Reason: Team managers know the staffing needs and should only decide requests for their own team.

### Decision: Show remaining balance on the form
<!-- symphony:decision={{ID:balance_display}} -->
- Owner: HR lead
- Impact: Low
- Status: Unresolved
- Reason: HR has not confirmed a reliable balance source. The form can launch with dates and request status while this is decided.

## Affected capabilities
- requirements
- team-calendar
