### READY — machine dependency gate

Factory sync has already validated scope, acceptance checks, proof, route, and
the dependency graph. Symphony keeps this state visible while dependencies are
open, then promotes the ticket to `Build` without starting an agent.
