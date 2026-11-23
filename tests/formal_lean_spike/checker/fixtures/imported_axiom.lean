import Dependency

-- The dependency's compiled theorem carries the custom axiom into the export.
theorem Graphlink.Spike.addZero (n : Nat) : n + 0 = n :=
  Graphlink.Dependency.viaAssumption n
