import Mathlib.Data.Nat.Basic

-- The external kernel needs propext to replay some primitive declarations.
-- This particular proof also uses it, so it fails the zero-axiom target policy.
theorem Graphlink.Spike.addZero (n : Nat) : n + 0 = n :=
  Eq.mp (propext (show True ↔ n + 0 = n from
    ⟨fun _ => Nat.add_zero n, fun _ => True.intro⟩)) True.intro
