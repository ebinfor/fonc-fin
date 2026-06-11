async def _boucle(self) -> None:
        """Boucle principale : collecte → détection → alertes → diffusion."""
        while self._running:
            t_start = time.monotonic()
            try:
                # self._db_factory invoquera get_db_ctx qui gère parfaitement le "async with"
                async with self._db_factory() as db:
                    await self._cycle(db)
                    
            except asyncio.CancelledError:
                break
            except Exception as exc:
                _log.warning("Monitoring cycle erreur: %s", exc)

            elapsed = time.monotonic() - t_start
            self._latences.append(elapsed * 1000)
            wait = max(0, POLL_INTERVAL_SEC - elapsed)
            await asyncio.sleep(wait)