import Lean
import LeanExport.Parse

open Lean

private def target : Name := `Graphlink.Spike.addZero

private def readExport (path : String) : IO LeanExport.ExportedEnv := do
  let handle ← IO.FS.Handle.mk path .read
  LeanExport.parseStream (.ofHandle handle)

private def getTheorem (env : LeanExport.ExportedEnv) : Except String TheoremVal := do
  let some (.thmInfo decl) := env.constMap[target]?
    | throw "Expected a theorem with the configured target name"
  return decl

-- Collect references from the kernel expression tree directly. Universe parameter
-- names and binder names are local to the declaration. A projection additionally
-- names the structure on which it operates. Do not expand shared subterms twice.
private partial def exprRefs (work : List Expr) (refs : Array Name)
    (seen : Std.HashSet Expr := {}) : Except String (Array Name) := do
  let e :: rest := work | return refs
  if seen.contains e then return ← exprRefs rest refs seen
  let seen := seen.insert e
  match e with
  | .const name _ => exprRefs rest (refs.push name) seen
  | .proj structName _ arg => exprRefs (arg :: rest) (refs.push structName) seen
  | .app fn arg => exprRefs (fn :: arg :: rest) refs seen
  | .forallE _ domain body _ | .lam _ domain body _ =>
    exprRefs (domain :: body :: rest) refs seen
  | .letE _ type value body _ => exprRefs (type :: value :: body :: rest) refs seen
  | .mdata _ value => exprRefs (value :: rest) refs seen
  | .bvar _ | .sort _ => exprRefs rest refs seen
  | .lit (.natVal _) => exprRefs rest (refs.push `Nat) seen
  | .lit (.strVal _) => exprRefs rest (refs.push `String) seen
  | .fvar _ | .mvar _ => throw "Unexpected open expression in an exported declaration"

-- Exhaustive over the eight ConstantInfo variants in the pinned kernel.
-- Conservatively include every named mutual-block member, even the advisory
-- 'all' field for ordinary definitions. A missing member requires a recheck.
private def declarationRefs (info : ConstantInfo) (refs : Array Name) : Except String (Array Name) :=
  match info with
  | .axiomInfo v => exprRefs [v.type] refs
  | .quotInfo v => exprRefs [v.type] refs
  | .defnInfo v => exprRefs [v.type, v.value] (refs ++ v.all.toArray)
  | .thmInfo v => exprRefs [v.type, v.value] (refs ++ v.all.toArray)
  | .opaqueInfo v => exprRefs [v.type, v.value] (refs ++ v.all.toArray)
  | .inductInfo v => exprRefs [v.type] (refs ++ v.all.toArray ++ v.ctors.toArray)
  | .ctorInfo v => exprRefs [v.type] (refs.push v.induct)
  | .recInfo v => do
    let mut refs ← exprRefs [v.type] (refs ++ v.all.toArray)
    for rule in v.rules do
      refs ← exprRefs [rule.rhs] (refs.push rule.ctor)
    return refs

-- Small positive checks on data, using two members of a fictitious mutual
-- block. These also typecheck the relevant projections at every image build.
private def familyExample : InductiveVal :=
  { (default : InductiveVal) with type := .sort .zero, all := [`Even, `Odd], ctors := [`Even.zero] }

private def ruleExample : RecursorVal :=
  { (default : RecursorVal) with
    type := .sort .zero
    all := [`Even, `Odd]
    rules := [{ ctor := `Odd.succ, nfields := 1, rhs := .proj `Pair 0 (.const `pairValue []) }] }

#guard (declarationRefs (.inductInfo familyExample) #[]).toOption == some #[`Even, `Odd, `Even.zero]
#guard (declarationRefs (.recInfo ruleExample) #[]).toOption == some #[`Even, `Odd, `Odd.succ, `Pair, `pairValue]

-- Traverse the exported declarations from this root, including types and bodies.
-- Each vertex is visited once; no intermediate axiom cache or collectAxioms
-- environment extension is read. A missing edge fails the measurement.
private partial def axiomsFrom (env : LeanExport.ExportedEnv) (todo : Array Name)
    (seen : Std.HashSet Name := {}) (axioms : Array String := #[]) :
    Except String (Array String) := do
  let some name := todo.back? | return axioms.qsort (· < ·)
  let todo := todo.pop
  if seen.contains name then return ← axiomsFrom env todo seen axioms
  let some info := env.constMap[name]? | throw "Incomplete export dependency closure"
  let next ← declarationRefs info todo
  let axioms := if info.isAxiom then axioms.push name.toString else axioms
  axiomsFrom env next (seen.insert name) axioms

def main (args : List String) : IO Unit := do
  let [challengePath, solutionPath] := args | throw (IO.userError "Expected two exports")
  let challenge ← readExport challengePath
  let solution ← readExport solutionPath
  let expected ← IO.ofExcept (getTheorem challenge)
  let actual ← IO.ofExcept (getTheorem solution)
  let axioms ← IO.ofExcept (axiomsFrom solution #[target])
  IO.println <| (Json.mkObj [
    ("target", toJson target.toString),
    ("challengeType", toJson (reprStr expected.type)),
    ("statementMatches", toJson (expected.toConstantVal == actual.toConstantVal)),
    ("axioms", toJson axioms)
  ]).compress
