# DitakNet production readiness plan

Այս փաստաթուղթը DitakNet-ի Docker/TrueNAS SCALE արտադրական պատրաստության
աշխատանքային պլանն ու կատարված փոփոխությունների մատյանն է։ Այն թարմացվում է
յուրաքանչյուր ավարտված ենթափուլից հետո։

## Ընդհանուր վիճակ

- Սկզբնական գնահատական՝ 65%
- Ընթացիկ փուլ՝ Փուլ 3 — Docker և TrueNAS SCALE, remote CI հաստատման փուլ
- Ընթացիկ գնահատական՝ 89%
- Փուլ 1-ի կարգավիճակ՝ ավարտված
- Փուլ 2-ի կարգավիճակ՝ ավարտված, GitHub quality/image-smoke run-ը հաջող է
- Production սերվերի գործարկում՝ չի կատարվում
- Git վիճակ՝ local/remote `main` համաժամեցված են, reviewed commit-ը push է արված առանց force-ի
- Թարմացման սկզբունք՝ versionավորված, backup-first, admin-confirmed, rollback-capable

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

- [~] `linux/amd64` և `linux/arm64` build/runtime smoke pipeline-ը պատրաստ և տեղական static validation անցած է․ իրական երկճարտարապետական GitHub runner-ի արդյունքը սպասվում է։
- [~] Docker bridge compose validation — definition/config-ը, deep health-ը, restart persistence-ը և legacy root-owned volume migration probe-ը պատրաստ են․ remote container run-ը սպասվում է։
- [~] TrueNAS host-network compose validation — definition/config և պաշտոնական library render-ը հաջող են, իսկ իրական TrueNAS host-ի deployment փորձարկումը մնում է Փուլ 5-ում։
- [x] Persistent mount-եր և permission ռազմավարություն — fresh Docker install-ի համար named volumes + սահմանափակ recursive initializer, bind path-երի համար explicit `568:568`, TrueNAS-ի համար Automatic Permissions/ACL տարբերակներ։
- [x] Container hardening — non-root `568:568`, read-only rootfs, PID limit, `no-new-privileges`, `cap_drop: ALL` և միայն `NET_RAW`։
- [x] Hash-locked Python/CI dependency set և dependency update policy։
- [~] Երկու architecture-ի SPDX SBOM/Trivy և digest-bound provenance/attestation pipeline-ը պատրաստ է․ SBOM remote run-ը սպասվում է, իսկ attestations-ը կստեղծվեն միայն առաջին նոր SemVer release publish-ի ժամանակ։
- [x] TrueNAS catalog metadata/schema validation — duplicate-key/static gate և pinned official `truenas/apps` library-ով 4 տարբերակի render։
- [x] Install, upgrade և rollback փաստաթղթեր՝ legacy bind → named-volume փոփոխության պարտադիր preflight-ով և offline recovery-ով։

## Փուլ 4 — Կայուն թարմացումներ (թիրախ՝ 96%)

- [ ] `stable` և `beta` update channel-ներ։
- [x] Signing key-ի առկայության դեպքում fail-closed signature verification՝ առանց unsigned fallback-ի։
- [ ] Production signing key provisioning, ստորագրված manifest-ի հրապարակում և default-ով պարտադիր signature policy։
- [~] Immutable exact-version GHCR tag guard — pipeline-ը պատրաստ է, առաջին remote run-ը սպասվում է։
- [ ] Հրապարակված digest-ի ստուգում և update manifest/release metadata-ում պահպանում։
- [ ] Update-ից առաջ պարտադիր SQLite backup։
- [ ] Version/migration compatibility validation։
- [ ] Admin confirmation և TrueNAS-ի կառավարելի update ճանապարհ։
- [ ] Health verification և rollback ընթացակարգ։

## Փուլ 5 — Վերջնական release validation (թիրախ՝ 100%)

- [ ] Մաքուր temporary Docker install։
- [ ] First-run setup՝ դատարկ database-ով։
- [ ] Նախորդ version-ից upgrade։
- [ ] Backup/restore և restart փորձարկումներ։
- [ ] Կեղծված manifest-ի end-to-end մերժում container/update հոսքում (unit regression coverage-ը պատրաստ է)։
- [ ] Անհաջող update-ի rollback փորձարկում։
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

## Հաջորդ հաշվետվություն

Փուլ 3-ի fast-forward push-ը, երկճարտարապետական GitHub Actions build/smoke/scan
արդյունքը, վերջնական test count-ը, cache cleanup-ը և Phase 3-ի ամփոփ կարգավիճակը։
