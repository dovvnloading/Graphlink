/**
 * The about-dialog island's state contract.
 *
 * See composer/bridgeTypes.ts for the fuller rationale (re-export from the
 * generated file, not a hand mirror). Every field here is a build-time
 * constant on the Python side - there is no live app state on this
 * surface at all.
 */
export type { AboutState } from "../../lib/bridge-core/generated/about-state";

import type { AboutState } from "../../lib/bridge-core/generated/about-state";

export const initialAboutState: AboutState = {
  schemaVersion: 1,
  minCompatibleSchemaVersion: 1,
  revision: 0,
  appName: "Graphlink",
  appVersion: "0.0.0-dev",
  repositoryUrl: "https://github.com/dovvnloading/Graphlink",
  developerName: "Matthew Robert Wesney",
  developerWebsiteUrl: "https://mattwesney.com",
  developerGithubUrl: "https://github.com/dovvnloading",
  copyrightText: "© 2026",
};
