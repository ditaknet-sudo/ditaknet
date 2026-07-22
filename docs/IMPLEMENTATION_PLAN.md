# DitakNet production readiness plan

Այս փաստաթուղթը DitakNet-ի Docker/TrueNAS SCALE արտադրական պատրաստության
աշխատանքային պլանն ու կատարված փոփոխությունների մատյանն է։ Այն թարմացվում է
յուրաքանչյուր ավարտված ենթափուլից հետո։

## Ընդհանուր վիճակ

- Սկզբնական գնահատական՝ 65%
- Ընթացիկ փուլ՝ Փուլ 4 — offline-only restore final audit-ի փոփոխություններից
  հետո ամբողջական local validation re-run-ը ընթացքի մեջ է, իսկ clean-clone/
  push/remote CI-ն դեռ pending են
- Ընթացիկ գնահատական՝ 95% (Փուլ 4-ի թիրախը՝ 96%, հաստատումից հետո)
- Մնացած փուլեր՝ 2՝ ներառյալ ընթացիկ Փուլ 4-ը, ապա Փուլ 5-ը։ Փուլ 4-ի
  հաստատումից հետո կմնա միայն 1 փուլ։
- Փուլ 1-ի կարգավիճակ՝ ավարտված
- Փուլ 2-ի կարգավիճակ՝ ավարտված, GitHub quality/image-smoke run-ը հաջող է
- Փուլ 3-ի կարգավիճակ՝ packaging/hardening/CI աշխատանքները ավարտված են, multi-arch remote run-ը հաջող է
- Փուլ 4-ի կարգավիճակ՝ signed-update/preflight/database/release/offline-restore
  code-ը պատրաստ է, բայց նոր architecture-ից հետո local quality/security gate-երի
  վերջնական re-run-ը, clean-clone ու GitHub push/CI-ն դեռ չեն ավարտվել
- Փուլ 5-ի կարգավիճակ՝ չի սկսվել․ իրական Docker/TrueNAS upgrade/rollback
  փորձարկումները մնում են
- Production սերվերի գործարկում՝ չի կատարվում
- Git վիճակ՝ Փուլ 4-ի աշխատանքային փոփոխությունները դեռ local են․ commit/push և
  նոր remote CI արդյունք այս հաշվետվության պահին չկան
- Repository path՝ նախկին `F:` կրիչը անհասանելի դառնալուց հետո նույն ամբողջական
  worktree-ն գտնվել և շարունակվում է `D:\SmartTech Monitoring Server\DitakNetMonitoring`-ում
- Թարմացման սկզբունք՝ signed/digest-bound, fail-closed, backup-first,
  admin-confirmed, external-redeploy, rollback-capable

## Կարգավիճակների նշանակությունը

- `[ ]` — չի սկսվել
- `[~]` — ընթացքի մեջ
- `[x]` — ավարտված և ստուգված
- `[!]` — արգելափակված կամ պահանջում է որոշում

## Փուլ 1 — Repository և անվտանգություն (թիրախ՝ 72%)

- [x] Ստեղծել մշտական պլան և աշխատանքների մատյան։
- [x] Համեմատել տեղական source-ը `ditaknet-sudo/ditaknet` GitHub repository-ի հետ։
- [x] Սահմանել մեկ հստակ source of truth և անվտանգ Git remote կառուցվածք։
- [x] Համաժամեցնել բացակայող `.github`, `tests`, `truenas`, `truenas-catalog` նյութերը՝ առանց տեղական նոր կոդը վերագրելու։
- [x] Հաստատել, որ `data`, `logs`, `backups`, secrets և cache ֆայլերը չեն մտնում source/release-ի մեջ։
- [x] Հաստատել, որ tracked/default config-ում իրական secret չկա և runtime secret-ը կարող է անվտանգ գեներացվել `data/`-ում։
- [x] Վերջնականացնել `.gitignore` և `.dockerignore` կանոնները։
- [x] Միատեսակեցնել app version, Git tag, GHCR tag, Release և update manifest տվյալները։
- [x] Կատարել Փուլ 1-ի static validation և հրապարակել հաշվետվությունը։

### Փուլ 1-ի ընդունման չափանիշներ

1. Repository-ում runtime database կամ գաղտնիք չկա։
2. Տեղական source-ի ոչ մի նոր ֆայլ չի կորել GitHub համեմատման ժամանակ։
3. Բոլոր version աղբյուրները նույն արժեքն ունեն։
4. Release source-ը վերարտադրելի է մաքուր checkout-ից․ ընթացիկ reviewed commit-ը նույնպես անցել է clean-clone verification։
5. Docker build context-ը չի ներառում development/runtime տվյալներ։

## Փուլ 2 — Թեստեր և CI (թիրախ՝ 82%)

- [x] Ընդլայնել test configuration-ը և մեկուսացված temporary database fixtures-ը։
- [x] Authentication/RBAC/CSRF թեստեր։
- [x] Database initialization և migration թեստեր։
- [x] Ping/TCP/HTTP checks և scheduler թեստեր։
- [x] Update manifest ու signature validation թեստեր։
- [x] Backup/restore compatibility թեստեր։
- [x] Docker build, production container `/health`/web smoke և SPDX SBOM՝ GitHub runner-ում հաջող։
- [x] `origin/main` պատմությունը պահպանող reviewed commit, clean-clone verification, fast-forward push և առաջին remote CI run։
- [x] GitHub Actions pipeline-ի սահմանում և static validation՝ quality, image-smoke և immutable publish gate-երով։

## Փուլ 3 — Docker և TrueNAS SCALE (թիրախ՝ 90%)

- [x] `linux/amd64` և `linux/arm64` build/runtime smoke pipeline — երկու architecture-ն էլ կառուցվել և անցել են remote runtime/security gate-երը։
- [x] Docker bridge compose validation — config, deep health, restart persistence և legacy root-owned volume migration probe-ը remote runner-ում հաջող են։
- [~] TrueNAS host-network compose validation — definition/config և պաշտոնական library render-ը հաջող են, իսկ իրական TrueNAS host-ի deployment փորձարկումը մնում է Փուլ 5-ում։
- [x] Persistent mount-եր և permission ռազմավարություն — fresh Docker install-ի համար named volumes + սահմանափակ recursive initializer, bind path-երի համար explicit `568:568`, TrueNAS-ի համար Automatic Permissions/ACL տարբերակներ։
- [x] Container hardening — non-root `568:568`, read-only rootfs, PID limit, `no-new-privileges`, `cap_drop: ALL` և միայն `NET_RAW`։
- [x] Hash-locked Python/CI dependency set և dependency update policy։
- [x] Երկու architecture-ի SPDX SBOM/Trivy և digest-bound provenance/attestation pipeline-ը պատրաստ է․ երկու SBOM/Trivy gate-երը remote run-ում հաջող են, իսկ attestations-ը կստեղծվեն առաջին նոր SemVer release publish-ի ժամանակ։
- [x] TrueNAS catalog metadata/schema validation — duplicate-key/static gate և pinned official `truenas/apps` library-ով 4 տարբերակի render։
- [x] Install, upgrade և rollback փաստաթղթեր՝ legacy bind → named-volume փոփոխության պարտադիր preflight-ով և offline recovery-ով։

## Փուլ 4 — Կայուն թարմացումներ (թիրախ՝ 96%)

- [x] Առանձին `stable` և `beta` update channel-ներ՝ անկախ URL-ներով, key
  scope-ով, cache trust policy-ով և monotonic anti-replay sequence-ով։
- [x] Strict schema-v2 Ed25519 manifest՝ default fail-closed signature policy-ով
  և առանց unsigned GitHub Releases fallback-ի։ Key rotation-ի համար մեկ channel-ում
  մի քանի public key/signature է թույլատրվում, իսկ private key-ը source-ում չի պահվում։
- [x] Exact GHCR SemVer tag-ը cryptographically կապվում է multi-arch index
  digest-ին և պարտադիր `linux/amd64`/`linux/arm64` child digest-ներին, ինչպես նաև
  source commit-ին, UTC publication time-ին, Release URL-ին ու compatibility policy-ին։
- [x] Immutable release workflow՝ նախ channel protected signing key-ի ստուգում,
  smoke-tested staging artifacts, OCI provenance/SBOM attestation verify, signed
  manifest, վերջում exact GHCR tag, GitHub Release manifest asset և ամենավերջում
  ընտրված channel feed-ի promotion։ Նույն digest-ով partial release-ի metadata
  repair-ը թույլատրված է, տարբեր digest-ով overwrite-ը՝ արգելված։
- [x] Update-ից առաջ պարտադիր backup format 2՝ member/final SHA-256,
  ZIP/SQLite quick/foreign-key validation և target version/digest/schema/channel/
  sequence-ին կապված operation context-ով։
- [x] Admin-only exact `UPDATE X.Y.Z` preflight՝ forced fresh manifest check,
  compatibility validation, validated backup և երկու ժամ գործող, յուրաքանչյուր
  բացման ժամանակ backup-ը նորից ստուգող auditable receipt։ Receipt-ը տալիս է միայն
  արտաքին Docker/TrueNAS/rollback հրահանգներ․ DitakNet-ը container չի redeploy անում։
- [x] Signed compatibility/managed preflight-ը ամբողջությամբ մերժում է
  `image_only` policy-ն՝ DB writer guard-ի հետ tag-only rollback-ը անվտանգ չլինելու
  պատճառով։ Թույլատրելի schema value-ներն են միայն `state_restore_required` և
  `unsupported`, իսկ վերջինը managed preflight-ը block է անում։
- [x] Database last-writer SemVer/schema/minimum-reader/migration-fingerprint
  guard-եր, future schema և unsafe downgrade-ի մերժում, migration-ից առաջ validated
  backup և `/health/deep` compatibility evidence։
- [x] Restore-ը դարձավ միայն offline․ web process-ը ամբողջ lifetime-ի ընթացքում
  պահում է mounted database-directory exclusive lock-ը, իսկ one-shot maintenance
  CLI-ն պարտադիր նույն lock-ը և mounts-ն է ստանում։ Legacy/pre-lock image-ները lock-ով
  չեն հայտնաբերվում, ուստի explicit stop-ը պարտադիր է։ Settings/setup live restore-ը disabled է։ CLI-ն
  պահանջում է exact backup SHA-256 ու `RESTORE <filename>`, ստեղծում է validated
  pre-offline snapshot և external JSON receipt, բայց restored DB-ն application-ով
  չի reopen/migrate/re-stamp անում՝ rollback marker-ները պահպանելու համար։
- [x] Offline swap-ը crash-atomic է․ pre-restore backup file+directory fsync,
  stopped current DB-ի `wal_checkpoint(TRUNCATE)`/sidecar cleanup/validation/fsync,
  staged DB validation/hash/fsync և մեկ վերջնական `os.replace` + directory fsync։
- [x] Backup upload/ZIP validation caps՝ compressed/uncompressed չափ, member count,
  per-member limit, compression ratio, unique safe paths և streamed hashes։ Web
  validation-ի blocking ZIP/SQLite աշխատանքը event loop-ից տեղափոխվել է worker thread։
- [x] Legacy root `update-manifest.json`-ը հստակ սահմանափակվել է schema-v1
  `2.0.1` amd64 artifact-ով․ այն schema-v2 managed handoff չի բացում։
- [!] Արտաքին production provisioning-ը դեռ պահանջվում է՝ `stable`/`beta`
  public key-եր committed keyring-ում, համապատասխան private-key secrets՝ protected
  GitHub environments-ում, `update-feed` branch protection և առաջին նոր SemVer
  release։ Ներկա keyring-ը դիտավորյալ դատարկ է, հետևաբար publish-ը fail-closed է։
- [~] Offline-only restore architecture-ից հետո ամբողջական local validation-ը
  նորից գործարկվում է․ վերջնական test count և quality/security արդյունքները դեռ
  pending են և այս checkpoint-ում ավարտված չեն հայտարարվում։
- [~] Phase 4-ի փակման համար դրանից հետո մնում են clean-clone verification-ը,
  reviewed commit/push-ը և remote GitHub CI result-ը։ Այս կետերը փակվելուց հետո
  կմնա միայն Փուլ 5-ը։

## Փուլ 5 — Վերջնական release validation (թիրախ՝ 100%)

- [ ] Մաքուր temporary Docker install՝ վերջնական նոր SemVer multi-arch artifact-ով։
- [ ] First-run setup՝ դատարկ database-ով։
- [ ] Իրական նախորդ version-ից signed stable/beta preflight և upgrade։
- [ ] Offline one-shot backup/restore, external receipt և restart փորձարկումներ։
- [ ] Կեղծված/replayed/wrong-channel manifest-ի end-to-end մերժում իրական
  container/update հոսքում (unit regression coverage-ը պատրաստ է)։
- [ ] Անհաջող update-ի պարտադիր state-restore rollback կամ `unsupported` policy-ի
  fail-closed մերժման փորձարկում։
- [ ] Իրական TrueNAS SCALE bridge/host-network install, update, deep-health և
  rollback validation՝ առանց production տվյալների օգտագործման։
- [ ] Release checklist-ի ամբողջական հաստատում։

## Աշխատանքների մատյան

### 2026-07-19

- Ստեղծվեց այս production-readiness պլանը։
- Հաստատվեց, որ աշխատանքը սկսվում է Փուլ 1-ից։
- Հաստատվեց, որ այս փուլում production սերվեր չի գործարկվելու։
- Նախնական audit-ով տեղական պատճենում հայտնաբերվել են runtime SQLite/WAL/SHM և Python cache ֆայլեր։ Դրանք չեն ջնջվում առանց առանձին անվտանգ պահպանման որոշման, բայց պարտադիր բացառվում են source/release-ից։
- Նախնական audit-ով տեղական պատճենում բացակայում են `.git`, `.github`, `tests`, `truenas` և `truenas-catalog` բաժինները։
- Git repository-ն initialization արվեց և `origin`-ի համար սահմանվեց `https://github.com/ditaknet-sudo/ditaknet.git` հասցեն։
- GitHub `main` archive-ը ստուգման պահին դատարկ էր, իսկ ամբողջական source-ը հասանելի էր `v2.0.1` tag-ում։ Այդ պատճառով համեմատման անվտանգ հիմք ընտրվեց immutable `v2.0.1` tag-ը, ոչ թե փոփոխական `main` branch-ը։
- `v2.0.1`-ի հետ համեմատությամբ՝ 251 ֆայլ նույնն էին, 39 տեղական ֆայլ փոփոխված/ավելի նոր էին, 16 release ֆայլ բացակայում էր։ Տեղական 39 ֆայլերից ոչ մեկը չվերագրվեց։
- Վերականգնվեցին բացակայող 16 ֆայլերը՝ GHCR workflow-ը, 2 սկզբնական test ֆայլերը, TrueNAS bridge/host compose-ները և catalog փաթեթը։
- `config/runtime.env`-ը ստուգվեց․ իրական password, token կամ session secret չի պարունակում։
- `.gitignore` և `.dockerignore` կանոնները արդեն բացառում են database, WAL/SHM, logs, backups, secrets և Python cache ֆայլերը source/release-ից։ Runtime ֆայլերը տեղում չեն ջնջվել։
- TrueNAS compose-ի fallback image-ը `latest`-ից փոխվեց immutable `2.0.1` version-ի՝ production-ում պատահական upgrade-ը կանխելու համար։
- Երկու TrueNAS compose տարբերակներն էլ անցան `docker compose config --quiet` ստուգումը։
- Վերականգնված սկզբնական test suite-ը գործարկվեց մեկուսացված temporary runtime պանակներով՝ առանց production սերվեր գործարկելու։ Արդյունք՝ **13 passed, 0 failed, 1 dependency deprecation warning**։
- Warning-ը FastAPI/Starlette test client-ի ապագա `httpx2` անցման մասին է և չի ձախողում ներկա runtime-ը։ Այն գրանցված է հետագա dependency compatibility աշխատանքի համար։

### 2026-07-20 — Փուլ 2

- Ստեղծվեց նախապես բեռնվող test isolation՝ temporary data/log/backup/plugin/database պանակներով, անջատված scheduler/update checker-ով և test session secret-ով։
- Ավելացվեցին authentication, password hashing, legacy hash, RBAC, permissions և CSRF regression թեստերը։
- Հեռացվեց `PYTEST_CURRENT_TEST` environment variable-ով CSRF-ի ամբողջական bypass-ը։
- CSRF dependency միացվեց HR, employee-presence և setup POST router-ներին, իսկ setup form-երը ստացան CSRF token injection։
- Ավելացվեցին database initialization/schema/idempotency թեստերը, և migration ledger-ը դարձավ deterministic։
- Ուղղվեց mixed numeric/text SemVer prerelease համեմատման `TypeError`-ը։
- Update manifest-ի embedded HMAC-ը դարձավ canonical և ստորագրվող՝ առանց `signature` դաշտի։ Ստորագրման բանալու առկայության դեպքում signature/manifest սխալը այլևս չի կարող անցնել unsigned fallback-ի։
- Ավելացվեցին Ping/TCP/HTTP և scheduler-ի 32 մեկուսացված թեստեր։ Իրական LAN/network չի օգտագործվել։
- Ուղղվեցին TCP refused retry և invalid scheduler retry-count վարքերը։
- Հաստատվեց և ուղղվեց critical SQLite WAL backup defect-ը։ Full և database-only backup-ները հիմա online snapshot են, իսկ restore-ից առաջ կատարվում է ZIP/manifest/SQLite integrity validation։
- Ավելացվեցին restore round-trip, failed restore rollback, corrupt backup rejection և explicit confirmation թեստերը։
- Ուղղվեց status badge-ի հնարավոր XSS-ը escape-aware Markup formatting-ով։ Dynamic table count-ի SQL-ը հաստատվեց hard-coded allowlist-ով սահմանափակված։
- Locale JSON-ում վերացվեց case-ambiguous key-ը և ռուսերենում լրացվեցին բացակայող 20 key-երը։ Բոլոր locale-ները հիմա ունեն նույն 1381 բանալին։
- CI pipeline-ը բաժանվեց `quality → image-smoke → publish` job-երի։ Publish token-ի `packages: write` իրավունքը տրվում է միայն վերջին job-ին։
- Ավելացվեցին exact version consistency, `pip check`, `pip-audit`, Bandit high-severity, secret-pattern, Python compile, pytest և երեք Compose validation gate-երը։
- Արտաքին GitHub Actions-ը pin արվեցին commit SHA-ներով։ Release job-երը serialize են արվում, floating `latest` tag չի փոխվում, իսկ publish job-ը ստանում է հենց smoke-test անցած image artifact-ը և պաշտպանում immutable version tag-ը։ SBOM/provenance-ը և transitive dependency hash-lock-ը տեղափոխվել են Փուլ 3-ի supply-chain hardening բաժին։
- Session-authenticated `/api/*` mutation-ների համար ավելացվեց CSRF header validation՝ bearer-token API client-ները չկոտրելով։
- Migration ledger-ի ID-ները դարձան SQL բովանդակությունից ստացվող կայուն hash-եր, և իրական legacy `ALTER TABLE` ուղին ծածկվեց regression թեստով։
- Test environment-ի բոլոր runtime path-երը պարտադիր ուղղվում են ժամանակավոր պանակներ՝ արտաքին production environment variable-ներից անկախ։
- Վերջնական local test արդյունք՝ **130 passed, 0 failed, 1 dependency deprecation warning**։
- Static Python compile՝ **159 ֆայլ**, release consistency՝ **OK 2.0.1**, root/TrueNAS bridge/TrueNAS host Compose validation՝ **բոլորը հաջող**։
- Իրական `data/ditaknet.db` և production server չեն օգտագործվել կամ փոփոխվել։
- Այդ checkpoint-ում local փոփոխությունները դեռ commit/push չէին արվել, ու առաջին GitHub CI run-ը մնում էր հաջորդ վերահսկվող քայլը։

### 2026-07-20 — ավարտական hardening և push-ի նախապատրաստում

- Remote `origin/main`-ի դատարկ tree-ով deletion commit-ը fetch/audit արվեց, և local branch-ը անվտանգ հիմնվեց դրա վրա որպես `main`՝ առանց force-push-ի կամ remote պատմության կորստի։
- Python settings-ի default-ը, Docker image-ը, երկու TrueNAS Compose տարբերակները և catalog template-ը դարձան explicit `APP_ENV=production`։ Test-only environment-ը մնաց միայն մեկուսացված test fixtures/CI job-ում։
- Runtime և combined CI dependency graph-երը ամբողջությամբ hash-lock արվեցին՝ համապատասխանաբար 672 և 940 SHA-256 hash-երով։ Docker/CI install-ը պարտադիր օգտագործում է `--require-hashes`։
- Ավելացվեց pinned `uv 0.11.29` lock refresh/check workflow և ամբողջ app+CI graph-ի dependency audit։
- CI test graph-ին ավելացվեց `httpx2==2.7.0`, և նախկին Starlette deprecation warning-ը վերացավ։
- Smoke-tested image-ից ավելացվեց SPDX SBOM, իսկ publish digest-ի համար՝ signed SLSA provenance և SBOM attestations։ Floating `latest` tag չի հրապարակվում։
- Fresh virtual environment validation՝ **131 passed, 0 failed, 0 warnings**, `pip check`՝ հաջող, dependency audit՝ **0 known vulnerabilities**։ Իրական SQLite DB-ի hash/mtime-ը չփոխվեց։
- Վերջնական static validation՝ dependency locks current, Python compile՝ **160 ֆայլ**, release/version consistency՝ **OK 2.0.1**, actionlint/Bandit/secret scan և երեք Compose config՝ հաջող։
- Reviewed commit-ի առանձին clean clone-ը նորից անցավ lock/release validation-ը և ամբողջ suite-ը՝ **131 passed, 0 failed, 0 warnings**։
- Մաքրվեցին workspace-ի **20 cache պանակ** և `%TEMP%`-ի **21 `ditaknet-*` test/tool պանակ**։ Վերջնական մնացորդը՝ **0**, իսկ իրական SQLite DB-ի SHA-256-ը մնաց անփոփոխ։
- `f7291b4` reviewed commit-ը fast-forward push արվեց `origin/main`՝ պահպանելով remote deletion commit-ի պատմությունը և առանց force-push-ի։
- GitHub Actions [run #29703679380](https://github.com/ditaknet-sudo/ditaknet/actions/runs/29703679380) ավարտվեց հաջող․ quality job-ի բոլոր gate-երը, production Docker build, `/health`/web smoke և smoke-tested image-ի SPDX SBOM generation-ը անցան։ Release publish job-ը սպասվածի պես skip արվեց `main` push-ի համար։

### 2026-07-20 — Փուլ 3, տեղական իրականացում

- Dockerfile-ը pin արվեց multi-architecture base digest-ով և դարձավ non-root `568:568` runtime։ Ավելացվեցին read-only root filesystem-ի համար նախատեսված runtime path-երը, production metadata-ն և build tooling-ի հեռացումը։
- CI-ն ընդլայնվեց առանձին `linux/amd64`/`linux/arm64` build-երով, QEMU runtime smoke-ով, `/health/deep` version/schema checks-ով, restart persistence-ով և երկու architecture-ի SPDX SBOM-ներով։
- Ավելացվեց Trivy-ի երկաստիճան քաղաքականություն՝ բոլոր high/critical finding-ների հաշվետվություն և fixable high/critical խնդիրների blocking gate։
- Publish հոսքը դարձավ transactional․ run-unique staging images/index → architecture ստուգում → signed provenance/SBOM attestations → վերջում immutable SemVer tag։ `latest` չի ստեղծվում կամ տեղափոխվում։
- Root Compose-ի fresh-install default-ը դարձավ չորս named volume։ Սահմանափակ `storage-init` ծառայությունը միայն այդ volumes-ի համար recursive, symlink-safe ownership migration է կատարում՝ `CAP_CHOWN`-ով։ Operator bind path-երը այդ root helper-ին երբեք չեն փոխանցվում։
- Հին Compose-ի `./data`, `./logs`, `./backups`, `./plugins` bind path-երը named-volume default-ով չկորցնելու համար README/upgrade/release փաստաթղթերում ավելացվեց պարտադիր source preservation, mount inspection և `568:568` permission preflight։ `--no-deps` upgrade հրահանգը հեռացվեց։
- TrueNAS bridge և host-network Compose-ները harden արվեցին՝ non-root, read-only, minimal capabilities, fail-fast host paths և offline restart-ի համար pull-if-missing վարքով։
- Catalog-ը համաժամեցվեց պաշտոնական generator metadata shape-ի հետ և ավելացվեցին bridge, host-network, Host Path automatic-permissions ու ACL test values։ Pinned `truenas/apps` commit/library-ով բոլոր 4 render-ները հաջող են։
- Պաշտոնական TrueNAS CDN icon URL-ը մնում է upstream reviewer-ից կախված արտաքին prerequisite։ Development pack-ը միտումնավոր պահում է repository-hosted SVG-ը և դա upstream PR-ից առաջ հստակ փաստաթղթավորված է։
- Ավելացվեցին install/upgrade/rollback, update/migration safety և GitHub release operation ուղեցույցները։ Հստակ գրանցվեց, որ հրապարակված `2.0.1`-ը legacy amd64 artifact է և նոր hardening/multi-arch հնարավորությունները պահանջում են նոր SemVer release։
- Մաքուր Python 3.11 hash-locked միջավայրում lock check, `pip check`, `pip-audit`, Bandit, compile և ամբողջ suite-ը հաջող են՝ **144 passed, 0 failed, 0 warnings**։
- Տեղական Docker daemon-ը անջատված է, production server չի գործարկվել։ Երկու architecture-ի իրական image build/smoke/scan-ը կկատարվի միայն push-ից հետո մեկուսացված GitHub Actions runner-ում։
- `379bd8f` implementation commit-ը fast-forward push արվեց `origin/main`։ Առաջին [remote run #29705296663](https://github.com/ditaknet-sudo/ditaknet/actions/runs/29705296663)-ում quality gate-ը և երկու architecture-ի build-երը հաջող էին, բայց smoke inspection assertion-ը չընդունեց Docker Engine 29-ի canonical `CAP_NET_RAW` անունը։ Application/runtime defect չէր գրանցվել։
- Assertion-ը normalize արվեց՝ `NET_RAW` և `CAP_NET_RAW` համարժեք ձևերը ընդունելով, միաժամանակ շարունակելով մերժել այլ capability-ները և privileged runtime-ը։ Ուղղումը commit արվեց `1e1654e`-ով և fast-forward push արվեց։
- Վերջնական [GitHub Actions run #29705551390](https://github.com/ditaknet-sudo/ditaknet/actions/runs/29705551390) ավարտվեց **success**․ quality/security/configuration gate, **144 passed**, `linux/amd64` և `linux/arm64` build-եր, non-root/read-only runtime smoke, legacy nested ownership migration, `/health/deep`, restart persistence, երկու architecture-ի Trivy report/blocking scan և SPDX SBOM generation՝ բոլորը հաջող։
- `main` push-ի publish job-ը սպասվածի պես **skipped** է․ այս փուլում Git tag, GitHub Release կամ նոր GHCR image չի հրապարակվել, և production server չի գործարկվել։
- Մաքրվեցին workspace-ի **19 cache պանակ** և `%TEMP%`-ի **9 `ditaknet-*` test/tool/log նյութ**։ Դրանք տեղափոխվեցին Windows Recycle Bin և վերականգնելի են։ Վերջնական մնացորդը՝ **0**։
- Իրական `data/ditaknet.db`-ը չօգտագործվեց և չփոփոխվեց․ SHA-256-ը մնաց `6B6F9F5CC4CB9601E72AEFB5A33F29961BFD2981D20264FAB157BAF68F7FFB40`, չափը՝ `6721536`, UTC mtime-ը՝ `2026-07-14T07:54:41.4443873Z`։

### 2026-07-22 — Փուլ 4, signed update և backup-first handoff

- Աշխատանքի ընթացքում նախկին `F:` կրիչը դարձավ անհասանելի։ Նույն Git history-ով,
  runtime տվյալներով և Փուլ 4-ի չcommit արված փոփոխություններով ամբողջական worktree-ն
  գտնվեց `D:\SmartTech Monitoring Server\DitakNetMonitoring`-ում, և աշխատանքը
  շարունակվեց այնտեղից՝ առանց վերասկսելու կամ ֆայլ կորցնելու։
- Ավելացվեց canonical `VERSION` source, իսկ root `update-manifest.json`-ը
  պահպանվեց որպես հրապարակված `2.0.1` legacy amd64 artifact-ի schema-v1 record։
  Phase 3/4 հնարավորությունները դեռ release չեն և պահանջում են նոր SemVer։
- Կառուցվեց strict schema-v2 metadata contract՝ առանձին `stable`/`beta`
  channel-ներով, Ed25519 public-key keyring/rotation-ով, canonical signing-ով,
  signed compatibility policy-ով և channel-scoped monotonic anti-replay sequence-ով։
- Manifest-ը պարտադիր կապում է exact պաշտոնական GHCR tag-ը multi-arch index
  digest-ին և `linux/amd64`/`linux/arm64` child digest-ներին, source commit-ին,
  GitHub Release URL-ին և UTC publication time-ին։ Unknown field/channel/key,
  bad signature/digest կամ stale sequence-ը fail-closed է։
- Update checker-ը բաժանվեց առանձին official stable/beta feed-երի, default
  signature-required policy-ով։ Cache reuse-ը կապվեց URL/channel/keyring/policy
  fingerprint-ին, իսկ unsigned fallback-ը չի կարող managed handoff բացել։
- Backup format-ը դարձավ 2՝ archive-ի անդամների և վերջնական ZIP-ի SHA-256-ներով,
  SQLite `quick_check`/`foreign_key_check` validation-ով և pre-update/
  pre-migration operation context-ով։ Failed validation-ի artifact-ը usable
  backup չի համարվում։
- Ավելացվեց admin-only update preflight․ admin-ը պետք է ճշգրիտ մուտքագրի
  `UPDATE <version>`, որից հետո կատարվում է fresh signed check, version/digest/
  compatibility validation, target-bound backup creation+revalidation և audit
  receipt-ի պահպանում։ Receipt-ը գործում է առավելագույնը երկու ժամ, backup-ը
  կրկին ստուգվում է բացելիս և տրամադրում է միայն արտաքին Docker/TrueNAS/
  rollback հրահանգներ։ Սերվերի կամ container-ի ավտոմատ գործարկում չի ավելացվել։
- `image_only` compatibility-ն հանվեց signed schema/preflight-ից, քանի որ
  persisted DB writer guard-ի դեպքում tag-only rollback-ի անվտանգությունը չի
  ապացուցվում։ Մնում են միայն `state_restore_required` և `unsupported`, ընդ որում
  `unsupported`-ը managed preflight-ը fail-closed block է անում։
- Database initialization-ը ստացավ persisted last-writer SemVer, schema
  revision, minimum reader և migration fingerprint guard-եր։ Future schema-ն ու
  unsafe app downgrade-ը մերժվում են state-ը փոխելուց առաջ, իսկ version transition-ի
  դեպքում migration-ից առաջ ստեղծվում ու ստուգվում է format-v2 backup։
- Final restore audit-ից հետո live database replacement-ը ամբողջությամբ
  արգելվեց։ Web process-ը mounted database-directory cross-process lock-ը պահում է
  ամբողջ lifetime-ի ընթացքում, իսկ offline CLI-ն նույն lock-ը non-blocking ստանալու
  ձախողման դեպքում restore-ը մերժում է։ Legacy/pre-lock image-ների դեպքում lock-ը
  activity չի կարող հայտնել, ուստի explicit stop-ը պարտադիր է։ Settings-ը միայն
  upload/validate և generated command է ցույց տալիս, setup-time live restore-ը նույնպես disabled է։
- State rollback-ի հերթը սահմանվեց fail-closed․ failed/new exact image-ը մնում է
  ընտրված → `docker compose stop ditaknet` → նույն image/mounts-ով one-shot
  `python -m ditaknet.offline_restore`՝ approved SHA-256 և exact
  `RESTORE <filename>` confirmation-ով → success-ից հետո միայն previous exact
  tag և `up -d`։ TrueNAS receipt-ի հերթը՝ Stop App → բոլոր recorded mounted
  datasets-ի recursive pre-update ZFS snapshot clone/rollback կամ failed/new image-ով
  same Data/Backups mounts one-shot → միայն հետո previous exact tag → start/deep-health։
- Offline restore-ը replacement-ից առաջ ստեղծում և validate է անում
  `ditaknet-pre-offline-restore-*` snapshot և fsync է անում file+directory-ն։ Հետո
  current DB-ն տեղում `wal_checkpoint(TRUNCATE)`/sidecar cleanup/validation/fsync է
  անցնում, staged DB-ն validate/hash/fsync է արվում, և կատարվում է մեկ վերջնական
  crash-atomic `os.replace` + directory fsync։
  Ավարտից հետո backup mount-ում գրվում է external JSON receipt՝ source/restored
  hash-երով։ Restore image-ը recovered DB-ն application initialization-ով չի reopen,
  migrate կամ re-stamp անում։
- Backup/ZIP ingestion-ին ավելացվեցին compressed/uncompressed չափի, member count-ի,
  per-member չափի և compression ratio-ի caps, duplicate/unsafe path rejection ու
  streamed checksum validation։ Web upload/validation-ի blocking ZIP/SQLite աշխատանքը
  offload է արվում worker thread՝ async event loop-ը չարգելափակելու համար։
- Release workflow-ը դարձավ channel-aware և tag-triggered․ protected signing
  key check-ը կատարվում է registry mutation-ից առաջ, հետո verify են արվում
  staging index/platform digest-երը, OCI provenance/SBOM attestations-ը և signed
  manifest-ը։ Exact SemVer tag-ից հետո ստեղծվում/repair է արվում GitHub Release-ն
  ու նույն manifest asset-ը, իսկ ընտրված channel feed-ը թարմացվում է ամենավերջում։
- Նույն source/digest-ով partial publication-ի metadata repair-ը նախատեսված է,
  բայց գոյություն ունեցող exact tag-ի տարբեր digest-ը երբեք չի overwrite արվում։
  GHCR `:latest` պատմական alias կարող է գոյություն ունենալ, սակայն այն unsupported
  է և ներկա workflow-ը չի ստեղծում կամ տեղափոխում այն։
- Updates UI-ից հեռացվեցին unsafe dynamic HTML կառուցումները, ավելացվեցին trust,
  schema, digest և backup-first preflight վիճակները, իսկ redeploy հրահանգները
  փակ են մինչև վավեր, չժամկետանց receipt-ը։
- Offline-only restore final audit-ից հետո ամբողջական local validation re-run-ը
  ավարտվեց՝ **216 passed, 0 failed, 1 տեղական warning**։ Warning-ը գալիս է միայն
  այս workstation-ի Starlette/httpx compatibility-ից․ CI lock-ը ներառում է
  `httpx2==2.7.0`։ Փոփոխված Python ֆայլերի Ruff format/check-ը, compileall-ը,
  JavaScript syntax check-ը, release/version consistency-ը, 7 JSON ֆայլերի parse-ը,
  actionlint-ը, Compose config-ը և TrueNAS upstream/catalog render validation-ը
  կանաչ են։
- Clean-clone verification-ը, commit/push-ը, CI-ի Bandit/pip-audit/secret-scan
  gates-ը և նոր GitHub Actions run-ը այս գրառման պահին դեռ pending են։
- Production signing material չի ստեղծվել կամ commit արվել։ Committed keyring-ի
  `stable` և `beta` բաժինները դիտավորյալ դատարկ են։ Արտաքին owner action է մնում
  public key provisioning-ը, protected environment private-key secrets-ը,
  `update-feed` branch protection-ը և առաջին նոր SemVer release-ի հաստատումը։
- Փուլերի մնացորդը այս checkpoint-ում 2 է՝ ընթացիկ Փուլ 4-ի validation/push/CI
  փակումը և Փուլ 5-ի իրական Docker/TrueNAS upgrade/rollback փորձարկումները։
  Փուլ 4-ը հաստատելուց հետո կմնա 1 փուլ։

## Հաջորդ հաշվետվություն

Հաջորդ հաշվետվությունը կփակի Փուլ 4-ը միայն full local validation re-run,
clean-clone verification, reviewed commit/push և հաջող remote GitHub CI-ից հետո։ Production
signing key provisioning-ը, protected environments/update-feed protection-ը և
առաջին նոր SemVer release-ը կմնան հստակ արտաքին prerequisite-ներ։ Դրանից հետո
կմնա միայն Փուլ 5-ը՝ իրական մեկուսացված Docker և TrueNAS SCALE install/upgrade/
rollback validation-ը։
