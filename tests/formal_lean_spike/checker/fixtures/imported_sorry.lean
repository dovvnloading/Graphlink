import Dependency

-- Only the imported theorem contains a placeholder.
theorem Graphlink.Spike.addZero (n : Nat) : n + 0 = n :=
  Graphlink.Dependency.unfinished n
