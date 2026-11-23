import Lean
import LeanExport.Parse
import Lake.Check.Util

open Lean

private def target : Name := `Graphlink.Spike.addZero

private def readExport (path : String) : IO LeanExport.ExportedEnv := do
  let handle ← IO.FS.Handle.mk path .read
  LeanExport.parseStream (.ofHandle handle)

private def getTheorem (env : LeanExport.ExportedEnv) : Except String TheoremVal := do
  let some (.thmInfo decl) := env.constMap[target]?
    | throw "Expected a theorem with the configured target name"
  return decl

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
  let gather : StateM (Array Name) Unit :=
    Lake.Check.runForUsedConsts info fun ref => modify (·.push ref)
  let next := (gather.run todo).2
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
