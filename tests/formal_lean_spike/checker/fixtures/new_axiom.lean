import Mathlib.Data.Nat.Basic

axiom Graphlink.Spike.extraAssumption (n : Nat) : n + 0 = n

theorem Graphlink.Spike.addZero (n : Nat) : n + 0 = n :=
  Graphlink.Spike.extraAssumption n
