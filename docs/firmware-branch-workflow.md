# Firmware submodule branch workflow

How branches, submodules, and gitlinks work across the firmware stack, and the
agreed workflow for custom PL fabric work. Written 2026-07-23.

## The stack

Four nested repos, all forks owned by LJO-S, all with `e200-custom` as the
integration branch:

```
antsdr-fmcw-radar                      (this repo)
 -> firmware/          = LJO-S/antsdr-fw-patch      branch e200-custom
     -> plutosdr-fw/   = LJO-S/adi-plutosdr-fw      branch e200-custom
         -> hdl/       = LJO-S/adi-libiio-hdl       branch e200-custom
         -> linux/     = LJO-S/adi-linux            branch e200-custom
         -> linux/     = LJO-S/adi-u-boot-xlnx      branch e200-custom
         -> buildroot/                              (upstream, pinned, no fork)
```

Almost all custom work lands in `hdl` (`projects/e200/`: rx_tap.vhd,
system_bd.tcl, Makefile, system_constr.xdc). Occasionally `linux`
(zynq-e200.dtsi for device-tree nodes) and `plutosdr-fw` (top Makefile).

## The mental model (the one thing to internalize)

A parent repo does NOT contain its submodule's files. It records exactly one
fact: "the submodule should be at commit SHA X" (a gitlink). Consequences:

- Committing in the parent NEVER saves submodule file changes. Work must be
  committed inside the repo where the files live, then the parent commits the
  updated pointer.
- Branches are per-repo. A branch in `hdl` is invisible to `plutosdr-fw`; it
  only ever sees SHAs.
- `git submodule update` (and clones) check out the pinned SHA directly ->
  detached HEAD. This is normal, but commits made while detached hang off no
  branch. After any `submodule update`, re-attach before working:
  `git switch <branch>` in each repo you edit.
- A branch ref protects commits from `resetGit.sh` (`reset --hard` moves HEAD,
  the branch keeps the commits; `git switch` brings them back). Nothing
  protects UNCOMMITTED files from `git clean -xdf`. Commit early, commit often.
- Pushing the branch (`git push -u origin <branch>`) in each fork is the
  off-machine backup. resetGit survives via commits; a dead disk survives via
  pushes.

## Branch strategy

- `e200-custom` in every fork = the integration branch: always buildable,
  always the thing a fresh clone should get (`.gitmodules` `branch =` entries
  point at it).
- Feature work happens on short-lived branches in `hdl` only:
  `feature/rx-tap`, `feature/axi-regs`, `feature/chirp-nco`, `feature/deramp`, ...
  Branch from `e200-custom`, commit at every working milestone, merge back
  when the feature is proven on hardware.
- `plutosdr-fw` and `firmware` do NOT get per-feature branches. They stay on
  `e200-custom` and just bump gitlinks at milestones. While an `hdl` feature
  branch is in flight, the parents show a dirty gitlink (`m`/`+`) - that is
  expected, ignore it.
- If `linux` (dtsi) gets touched: same rule as `hdl` - make a local branch
  first (`git switch -c e200-custom`), because resetGit hard-resets it too.

## The milestone ritual (bottom-up pointer bump)

When a feature merges into `hdl`'s `e200-custom`:

```
cd firmware/plutosdr-fw/hdl
git switch e200-custom && git merge feature/<name>       # fast-forward usually
git push origin e200-custom

cd ..                       # plutosdr-fw
git add hdl && git commit -m "hdl: <what the milestone is>"
git push origin e200-custom

cd ..                       # firmware (antsdr-fw-patch)
git add plutosdr-fw && git commit -m "fw: <same>"
git push origin e200-custom

cd ..                       # antsdr-fmcw-radar
git add firmware && git commit -m "firmware: <same>"
```

Order matters (innermost first): each parent can only point at a commit that
already exists in its child. A fresh `git clone --recurse-submodules` of the
radar repo then reproduces the exact working stack.

## When to run resetGit.sh

Basically never. It hard-resets all five repos to the PRISTINE UPSTREAM SHAs -
one commit BEFORE the "checkpoint: patched v0.39 baseline" commits - so it
strips the MicroPhase E200 patches too (it even rm -rf's library/axi_vcxo_ctrl).
It belongs to MicroPhase's reset -> patch.sh -> build cycle, which the fork
structure replaced: the patched state is committed on e200-custom, and setup.sh
re-patches the three unforked submodules on a fresh clone.

Instead:
- Clean build cruft: `sudo -E make clean` in plutosdr-fw, or targeted
  `git clean -df` (no -x) in the dirty repo. Never -x-clean buildroot: it
  wipes the dl/ download cache (an hour of re-fetching).
- Back to known-good: `git switch e200-custom && git reset --hard
  origin/e200-custom` in the affected fork; re-run setup.sh if
  linux/buildroot/u-boot-xlnx were cleaned (their patches sit uncommitted).
- The one real use: migrating to a new upstream firmware release (v0.40+).
  Commit + push everything first, reset, apply new patches, new checkpoint
  commits, rebase/cherry-pick feature/* work on top. Planned surgery, not routine.

If it does run: all five repos end up detached at upstream SHAs, patches gone;
committed work survives (git switch e200-custom in the forks, setup.sh for the
rest), uncommitted work does not.

## Noise to not commit

- `hdl/library/*/component.xml`: Vivado stamps `coreCreationDateTime` on every
  build. Discard before committing: `git checkout -- library/`.
- `hdl/projects/e200/` build products (e200.cache, e200.runs, *.log, ...):
  already untracked; leave them untracked.
- `plutosdr-fw/build_sdimg/*`: build outputs, unfortunately tracked since the
  baseline checkpoint. Either commit them at SD-flash milestones (poor man's
  image archive) or `git rm -r --cached build_sdimg` + .gitignore them.
  Decide once; don't let them bleed into feature commits.
