import Mathlib.Data.Nat.Basic

namespace Graphlink.Dependency

axiom assumed (n : Nat) : n + 0 = n

theorem viaAssumption (n : Nat) : n + 0 = n := assumed n

theorem unfinished (n : Nat) : n + 0 = n := by
  sorry

end Graphlink.Dependency
