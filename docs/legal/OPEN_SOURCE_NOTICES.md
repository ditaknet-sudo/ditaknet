# Third Party Open Source Notices — DitakNet

**Status:** Draft — generated from local environment scan; verify against production Docker image before release
**Last Updated:** July 3, 2026

DitakNet includes third-party open source components. This notice is provided for transparency and does not modify the DitakNet Software License Agreement (EULA).

## Monitoring server (Python)

Major runtime dependencies and their licenses:

| Component | Version | License | URL |
| --- | ---: | --- | --- |
| FastAPI | 0.138.x | MIT | https://github.com/fastapi/fastapi |
| Starlette | 1.3.x | BSD-3-Clause | https://github.com/Kludex/starlette |
| Uvicorn | 0.49.x | BSD-3-Clause | https://uvicorn.dev/ |
| Pydantic | 2.13.x | MIT | https://github.com/pydantic/pydantic |
| Jinja2 | 3.1.x | BSD-3-Clause | https://github.com/pallets/jinja/ |
| APScheduler | 3.11.x | MIT | https://apscheduler.readthedocs.io/ |
| aiosqlite | 0.22.x | MIT | https://aiosqlite.omnilib.dev |
| httpx | 0.28.x | BSD-3-Clause | https://github.com/encode/httpx |
| loguru | 0.7.x | MIT | https://github.com/Delgan/loguru |
| python-dotenv | 1.2.x | BSD-3-Clause | https://github.com/theskumar/python-dotenv |
| python-multipart | 0.0.x | Apache-2.0 | https://github.com/Kludex/python-multipart |
| PyYAML | 6.0.x | MIT | https://pyyaml.org/ |
| psutil | 7.2.x | BSD-3-Clause | https://github.com/giampaolo/psutil |
| itsdangerous | 2.2.x | BSD-3-Clause | https://github.com/pallets/itsdangerous/ |

## Public website (Node.js)

| Component | License | Notes |
| --- | --- | --- |
| Next.js | MIT | https://nextjs.org/ |
| React | MIT | https://react.dev/ |
| Prisma | Apache-2.0 | https://www.prisma.io/ |
| Tailwind CSS | MIT | https://tailwindcss.com/ |
| Zod | MIT | https://zod.dev/ |
| Lucide React | ISC | https://lucide.dev/ |

Run `npx license-checker --production --summary` in `DitakNet/` before release to refresh this section.

## Regenerating this file

### Python (monitoring server)

```bash
pip install pip-licenses
pip-licenses --from=mixed --with-urls --format=markdown > THIRD_PARTY_NOTICES_PYTHON.md
```

### Node (website)

```bash
cd DitakNet
npx license-checker --production --summary
npx license-checker --production --json > THIRD_PARTY_NOTICES_FRONTEND.json
```

### Docker image

```bash
trivy image --scanners license your-image:tag
```

## License risk review

Generally acceptable for proprietary distribution: MIT, BSD-2-Clause, BSD-3-Clause, Apache-2.0, ISC, PSF-2.0.

Review carefully before release: GPL, AGPL, LGPL (linking/deployment dependent), SSPL, Commons Clause, unknown/custom licenses.

## Disclaimer

Versions above reflect a development environment scan. Production release builds should regenerate this notice from the exact pinned dependencies in the shipping Docker image and website lockfile.
