# Epidemiology and Biostatistics — Biology Workbench

Plugin ID: `science_epidemiology`; pack ID: `epidemiology`. Enable the pack in **Settings → Plugins**, then select **Biology Workbench → Epidemiology and Biostatistics**. This grant-gated pack is independent of existing Biology operations and does not change their identifiers, results or saved receipts. All calculations use Python's standard library and the shared Workbench input and result contracts.

## Operations and measurement units

Every operation has declared Workbench inputs, a runnable example, method citations, an output summary and scalar values with companion unit labels. Rates and their confidence limits share the same denominator and chosen display scale. All reported confidence intervals are two-sided **95%**, unless an estimate is undefined.

| Operation | Inputs | Outputs | Method |
| --- | --- | --- | --- |
| `two_by_two_measures` | Treated/exposed events and non-events, control/unexposed events and non-events (people) | Observed risks (probabilities), RR, OR (dimensionless), RD and ARR (probabilities), NNT/NNH (people), with 95% intervals | Katz/Woolf log-ratio CIs; Newcombe unpooled Wilson-score RD CI; reciprocal ARR interval |
| `diagnostic_test_accuracy` | TP, FP, FN, TN (people), using disease status as reference | Sensitivity, specificity, PPV, NPV, accuracy, prevalence (probabilities) and 95% Wilson intervals; LR+ and LR− (dimensionless) | Binomial proportions and likelihood ratios |
| `roc_curve_auc` | Paired diagnostic scores (dimensionless), 0/1 disease status and higher/lower positive-score direction | ROC plot and threshold table of FPR/TPR (probabilities), AUC and Youden's J (dimensionless) | Tie-aware thresholds, trapezoidal/Mann–Whitney AUC |
| `incidence_rate` | Incident events, positive observed person-time, person-time label, positive display multiplier | Event rate and exact Poisson 95% limits per chosen person-time units | Garwood chi-square/gamma limits |
| `direct_age_standardization` | Events, person-time, and reference-population counts in **matching age-stratum order**, person-time label, multiplier | Crude and directly standardized rates, SE, and Fay–Feuer gamma 95% interval, age-specific table and plot | Directly weighted Poisson rates |

## Two-by-two event risks and NNT/NNH

Denote treated/exposed events by a, treated non-events by b, control/unexposed events by c, and control non-events by d. Require a+b > 0 and c+d > 0. The **observed** group risks are pT=a/(a+b) and pC=c/(c+d); RR=pT/pC, OR=ad/(bc), RD=pT−pC, and ARR=pC−pT=−RD.

RR 95% CI is exp(log(RR) ± 1.95996398454·SE), with SE²=1/a−1/(a+b)+1/c−1/(c+d). OR 95% CI is exp(log(OR) ± 1.95996398454·SE), with SE²=1/a+1/b+1/c+1/d. **If any cell is zero**, the implementation adds 0.5 to *each* cell when calculating **both** point estimates and their ratio CIs; observed risks, RD and ARR remain uncorrected. Ratio intervals are log-normal approximations, and sparse counts may warrant a different analysis.

RD uses Newcombe's unpooled Wilson-score interval. For Wilson limits (LT,UT) on pT and (LC,UC) on pC, RD lower is RD−sqrt((pT−LT)²+(UC−pC)²), and upper is RD+sqrt((UT−pT)²+(pC−LC)²), clipped to [−1,1]. This avoids naive Wald intervals at the boundaries. The absolute reciprocal 1/|ARR| is labeled **NNT** for a positive ARR and **NNH** for a negative ARR, reported as a continuous value rather than silently rounding. Invert the ARR 95% interval to get the NNT/NNH 95% interval only when that interval lies entirely on one side of zero. If it includes zero, the reciprocal confidence set is unbounded (possibly containing benefit and harm branches), and output bounds are explicitly marked unbounded. Equal risks produce undefined NNT.

**Worked example:** a=12, b=188, c=30, d=170: RR=0.4 (95% CI 0.210891–0.758685); OR=0.361702 (0.179462–0.729003); RD=−0.09 (−0.151520–−0.030037); NNT=11.1111 (6.59977–33.29258). Relabeling treatment and control reverses the RD sign and yields NNH of equal magnitude. An observational exposed/unexposed table does **not** by itself establish a causal treatment effect.

## Diagnostic test accuracy

Sensitivity=TP/(TP+FN); specificity=TN/(TN+FP); PPV=TP/(TP+FP); NPV=TN/(TN+FN); accuracy=(TP+TN)/N; prevalence=(TP+FN)/N. Each reported proportion has a Wilson-score 95% interval, including all-success and zero-success numerators. LR+=sensitivity/(1−specificity), and LR−=(1−sensitivity)/specificity. An empty denominator produces **undefined** estimates and CI bounds; a zero likelihood-ratio denominator produces **infinite** only if its numerator is positive (otherwise it is undefined 0/0). No arbitrary smoothing is applied. The total sample must be nonempty; estimating sensitivity requires at least one diseased participant, and estimating specificity requires at least one non-diseased participant.

**Worked example:** TP=80, FP=10, FN=20, TN=90: sensitivity=0.8 (Wilson 95% CI 0.711171–0.866633), specificity=0.9, PPV=0.888889, NPV=0.818182, accuracy=0.85, LR+=8, LR−=0.222222. Predictive values depend on study prevalence and may not transport to a population with different prevalence.

## Empirical ROC and AUC

Supply equal-length score and binary reference-status arrays, with at least one case and one control. The direction choice indicates whether **higher** or **lower** scores correspond to disease; with lower-positive scores, sorting is reversed but case labels do not change. All tied scores enter the predicted-positive group at once, at an inclusive threshold. The ROC begins at (FPR,TPR)=(0,0) and ends at (1,1). AUC is the area under the resulting empirical stepwise/trapezoidal plot; equivalently it equals the fraction of all case–control pairs correctly ordered, awarding 0.5 for an exact tie. This implements the Mann–Whitney interpretation without randomized tie-breaking or a normality assumption.

Report the first (most stringent) maximizer of Youden J=TPR−FPR as an **in-sample descriptive** threshold, not an externally validated cutoff. AUC uses every observed threshold even when the display samples points to respect the Workbench's 1,024-point limit. This operation does not claim an AUC confidence interval.

**Worked example:** scores [0.91,0.8,0.8,0.55,0.4,0.1] with disease status [1,1,0,1,0,0] give AUC=5/6; the tied 0.8 observations contribute half credit, and the best Youden threshold is 0.55.

## Incidence rates and exact Poisson intervals

With k incident events and positive measured person-time T, the rate is (k/T)×multiplier. The **central two-sided exact Garwood 95% interval** divides the chi-square 0.025 quantile with 2k degrees of freedom by 2T for the lower bound, and the chi-square 0.975 quantile with 2(k+1) degrees of freedom by 2T for the upper bound. The lower bound is zero for k=0, and that case's upper bound is **−ln(0.025)/T** (not −ln(0.05)/T, which is one-sided). The implementation numerically inverts the regularized gamma CDF using the Python standard library and makes no normal approximation. Events are limited to 1,000,000 for reliable bounded computation.

**Worked examples:** k=0 in T=100 person-years gives a 95% upper rate of 36.8888 per 1,000 person-years; k=15 in T=2500 person-years gives rate 6 per 1,000 (Garwood 95% CI 3.358154–9.896088). Assumptions: independent Poisson event counts and fixed observed person-time; repeat events from the same individual may require models accommodating dependence.

## Direct age standardization

For aligned strata i, with observed events kᵢ, positive person-time Tᵢ, and nonnegative standard-population counts sᵢ, normalize wᵢ=sᵢ/(sum of all s). A positive total reference population is required. The standardized rate is R=Σwᵢ kᵢ/Tᵢ. Under independent Poisson counts, its plug-in variance is V=Σkᵢ(wᵢ/Tᵢ)², SE=sqrt(V), and b=max(wᵢ/Tᵢ). The **Fay–Feuer gamma 95% limits** are:

- Lower 0 when R=0, else GammaQuantile(0.025; shape R²/V, scale V/R).
- Upper GammaQuantile(0.975; shape (R+b)²/(V+b²), scale (V+b²)/(R+b)).

Scale rate, SE and both CI endpoints together. Fay–Feuer limits for unequal coefficients are gamma-based approximations, not generally exact Poisson intervals; for equal coefficients wᵢ/Tᵢ they reduce to the exact pooled Garwood limits. All input vectors must have matching age strata in the same order. Missing strata are not imputed; person-time units are labels and are not automatically converted. Zero reference weight is permitted in individual strata; zero total weight, nonpositive person-time and more than 1,000,000 total events are rejected.

**Worked example:** events [5,15,30], person-time [1000,2000,1500] person-years, standard counts [4000,3500,2500]. The standardized rate is 962.5 per 100,000 person-years, Fay–Feuer 95% CI 700.0868–1317.3444, compared with crude rate 1111.1111 per 100,000.

## Method references and regression evidence

The associated tests, `backend/tests/test_scientific_epidemiology.py`, check worked numeric results, independent case–control AUC pair enumeration, exact Poisson zero/one-count identities, gamma-tail inversion, the equal-coefficient reduction of Fay–Feuer to exact pooled Garwood, swapped groups/strata, invalid observations, and Workbench result receipt verification. These are regression oracles, not validation that study assumptions hold.

- Newcombe (1998), confidence intervals for independent proportions: https://pubmed.ncbi.nlm.nih.gov/9595617/
- Wilson (1927), binomial score inference: https://doi.org/10.1080/01621459.1927.10502953
- Altman (1998), intervals for number needed to treat: https://www.bmj.com/content/317/7168/1309
- Hanley & McNeil (1982), ROC AUC interpretation: https://pubmed.ncbi.nlm.nih.gov/7063747/
- Garwood (1936), exact Poisson confidence limits: https://doi.org/10.1093/biomet/28.3-4.437
- Fay & Feuer (1997), gamma intervals for directly standardized rates: https://pubmed.ncbi.nlm.nih.gov/9131766/
- SAS/STAT, Fay–Feuer direct-standardization gamma CI equations: https://support.sas.com/documentation/cdl/en/statug/67523/HTML/default/statug_stdrate_details14.htm
