# Project overview

## What ASRE-Lab is

ASRE-Lab stands for Autonomous Smart Reverse Engineering Laboratory.

It is an engineering research platform built around one practical idea: compare controlled design alternatives instead of inspecting one design in isolation.

The platform connects design generation, physics, evidence, analysis, human review, and reporting inside one persisted study.

## Where the idea came from

The original project started from a research question about the engineering characteristics of the Giza pyramids.

There are many claims about why the pyramids were built. I did not want to choose one claim first and then search for evidence that supported it. I wanted to study the geometry itself.

The question became:

> If a structure was intended to perform well for a physical purpose, what geometry would perform best and how close would the original structure be to that geometry?

A physical study with many accurate models was not realistic. It would require materials, equipment, lab capacity, and time. That pushed the project toward a digital system.

## The real problem

The main problem was not CAD generation.

One geometry gives one geometry and one result. That makes it hard to know whether a feature is important.

The useful information appears when a parameter changes and the rest of the experiment stays controlled.

For example, a study can vary pyramid height while keeping the base size, material, solver, boundary conditions, and numerical settings fixed. The resulting differences can then be compared directly.

This became the core method behind ASRE-Lab.

## Reverse engineering by contrast

The original design is a reference point.

ASRE-Lab creates controlled alternatives around it and runs the same declared model on those alternatives.

The comparison can show:

- which parameters have the strongest effect
- which changes improve or worsen a target metric
- whether good designs share common features
- where trade-offs appear
- whether an apparent relationship is consistent across the tested design space

The method does not prove design intent. It does not show why an original engineer or builder made a choice. It gives the researcher measurable physical behavior to investigate.

## Why the project became larger than one simulation

The analysis goal came first.

To compare many designs, the system needed a repeatable way to create them. That led to parametric design generation and CAD artifacts.

To compare physical behavior, the designs needed numerical results. That led to the solver registry, bounded engineering models, durable simulation jobs, and field artifacts.

Once many results existed, they needed to stay connected to the designs and conditions that produced them. That led to persisted evidence, analysis provenance, Scientific Trust, human decision records, and reports.

The current workflow is:

```text
Question
-> controlled design space
-> CAD artifacts
-> physics setup
-> validation
-> durable simulations
-> results and evidence
-> analysis
-> Scientific Trust
-> human decision
-> report
```

## Current product

The deployed product includes:

- public landing and scientific-scope pages
- Supabase authentication
- a persisted research dashboard
- study creation and reopen
- structured parametric design input
- design-space preview and variant generation
- STEP and STL artifacts for supported designs
- bounded solver configuration
- pre-run comparison of variables and controlled conditions
- async simulation execution
- persisted scalar results and field artifacts
- comparative analysis
- evidence records and Scientific Trust
- explicit human decision actions
- persisted research reports
- authenticated report, analysis, simulation, CAD, and field downloads
- recovery after refresh and sign-in

The main guided v1 study is a controlled square-pyramid thermal workflow.

The backend contains a broader set of bounded solver capabilities, including CAD-derived 3D research models.

## Main design rule

The software should not claim more than the code and evidence support.

A solver returning a number is not enough.

The platform keeps the model, inputs, assumptions, validity, convergence, provenance, evidence, and limitations around the result so a later reviewer can see how it was produced.

## AI in the project

AI is not used as a replacement for numerical physics.

It can help with supported natural-language design interpretation and explanation of existing information.

It does not create physical evidence and it does not approve engineering decisions.

## What the project is not

ASRE-Lab is not a general-purpose industrial engineering suite.

It does not claim unrestricted 3D FEA, unrestricted CFD, turbulence, general nonlinear materials, industrial certification, or general multiphysics.

Some backend capabilities use real 3D CAD-derived FEM or CFD, but they remain bounded by declared geometry, model, mesh, material, and validation limits.

## Current goal

The v1 goal is a stable research product that can be inspected and understood by another person.

Future work is intentionally separate from v1. A competition-focused v2 can add stronger visualization, deeper validation, and new research capabilities without turning the first release into an endless development cycle.
