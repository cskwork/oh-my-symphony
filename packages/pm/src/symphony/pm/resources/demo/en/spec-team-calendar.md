# Team calendar

## ADDED Requirements

### Requirement: Show approved leave on the team calendar
<!-- symphony:req={{ID:approved_calendar}} -->

The system SHALL show an employee's Approved leave on their team's calendar for every included date. It MUST exclude Pending and Declined requests and requests from other teams.

#### Scenario: Calendar shows only approved leave for the team
<!-- symphony:scenario={{ID:calendar_approved_only}} -->
- **GIVEN** the team has Approved leave on 2026-11-02 and separate Pending and Declined requests, and another team also has Approved leave
- **WHEN** a signed-in teammate opens the team's November calendar
- **THEN** the calendar shows the team's Approved leave on 2026-11-02 and excludes all other requests
