import logging
import requests
import signal
import time
from datetime import timedelta

from celery import shared_task
from django.utils import timezone
from django.conf import settings

from observability import traced
from ..models import DecodoIPBlock, DecodoIP, ProxyServer

logger = logging.getLogger(__name__)


# --------------------------------------------------------------------------- #
#  Decodo IP Block Sync Task
# --------------------------------------------------------------------------- #

@shared_task(bind=True, ignore_result=True)
def sync_ip_block(self, block_id: str) -> None:
    """
    Sync IP addresses for a specific Decodo IP block.

    This task fetches IP information from the Decodo API for each port
    in the block and updates the database with the latest ISP and location data.

    Args:
        block_id: UUID of the DecodoIPBlock to sync
    """
    with traced("PROXY Sync IP Block", block_id=block_id):
        try:
            # Fetch the IP block with its credential
            ip_block = DecodoIPBlock.objects.select_related("credential").get(pk=block_id)
            logger.info("Starting sync for IP block: %s", ip_block)

            # Get credentials
            username = ip_block.credential.username
            password = ip_block.credential.password

            # Track sync statistics
            updated_count = 0
            created_count = 0
            error_count = 0

            # Sync each IP in the block
            for port_offset in range(ip_block.block_size):
                with traced("PROXY Sync IP", block_id=block_id, port_offest=port_offset):
                    try:
                        port = ip_block.start_port + port_offset
                        logger.info("Processing IP %d/%d for block %s (port %d)",
                                   port_offset + 1, ip_block.block_size, ip_block, port)

                        # Make request to Decodo API
                        ip_data = _fetch_decodo_ip_data(
                            username=username,
                            password=password,
                            endpoint=ip_block.endpoint,
                            port=port
                        )

                        if ip_data:
                            # Update or create IP record
                            was_created = _update_or_create_ip_record(ip_block, ip_data, port)
                            if was_created:
                                created_count += 1
                            else:
                                updated_count += 1
                        else:
                            error_count += 1
                            logger.error("Failed to fetch IP data for port %d", port)

                    except Exception as e:
                        logger.error("Error syncing port %d for block %s: %s", port, block_id, str(e))
                        error_count += 1

            logger.info(
                "Sync completed for block %s: %d created, %d updated, %d errors",
                block_id, created_count, updated_count, error_count
            )

        except DecodoIPBlock.DoesNotExist:
            logger.error("DecodoIPBlock %s not found", block_id)
        except Exception as e:
            logger.exception("Error syncing IP block %s: %s", block_id, str(e))


def _fetch_decodo_ip_data(username: str, password: str, endpoint: str, port: int) -> dict | None:
    """
    Fetch IP data from Decodo API for a specific proxy endpoint and port.

    Args:
        username: Decodo username
        password: Decodo password
        endpoint: Proxy endpoint (e.g., "isp.decodo.com")
        port: Proxy port number

    Returns:
        Dictionary with IP data from Decodo API, or None if request failed
    """
    with traced("PROXY Fetch Decodo IP Data", endpoint=endpoint, port=port):
        try:
            # Configure proxy and authentication
            proxy_url = f"http://{username}:{password}@{endpoint}:{port}"
            proxies = {
                "http": proxy_url,
                "https": proxy_url
            }

            logger.info("Making API request via proxy %s:%d to https://ip.decodo.com/json", endpoint, port)

            # Make request to get IP information
            response = requests.get(
                "https://ip.decodo.com/json",
                proxies=proxies,
                timeout=30,
                headers={"User-Agent": "gobii-sync/1.0"}
            )
            response.raise_for_status()

            data = response.json()

            # Extract key information for logging
            ip_address = data.get("proxy", {}).get("ip", "unknown")
            isp_name = data.get("isp", {}).get("isp", "unknown")
            country_name = data.get("country", {}).get("name", "unknown")
            city_name = data.get("city", {}).get("name", "unknown")

            logger.info("Successfully fetched IP data for %s:%d - IP: %s, ISP: %s, Location: %s, %s",
                       endpoint, port, ip_address, isp_name, city_name, country_name)
            return data

        except requests.RequestException as e:
            logger.error("Failed to fetch IP data for %s:%d - %s", endpoint, port, str(e))
            return None
        except (ValueError, KeyError) as e:
            logger.error("Invalid JSON response for %s:%d - %s", endpoint, port, str(e))
            return None
        except Exception as e:
            logger.error("Unexpected error fetching IP data for %s:%d - %s", endpoint, port, str(e))
            return None


def _update_or_create_ip_record(ip_block: DecodoIPBlock, ip_data: dict, port: int) -> bool:
    """
    Update or create a DecodoIP record with the provided data.

    Args:
        ip_block: The DecodoIPBlock this IP belongs to
        ip_data: Dictionary with IP data from Decodo API
        port: The port number used to discover this IP

    Returns:
        True if a new record was created, False if existing record was updated
    """
    with traced("PROXY Update or Create IP Record", ip_block_id=str(ip_block.id), port=port):
        try:
            # Extract IP address from the proxy data
            ip_address = ip_data.get("proxy", {}).get("ip")
            if not ip_address:
                logger.error("No IP address found in API response")
                return False

            # Extract ISP data
            isp_data = ip_data.get("isp", {})
            isp_name = isp_data.get("isp", "")
            isp_asn = isp_data.get("asn")
            isp_domain = isp_data.get("domain", "")
            isp_organization = isp_data.get("organization", "")

            # Extract city data
            city_data = ip_data.get("city", {})
            city_name = city_data.get("name", "")
            city_code = city_data.get("code", "")
            city_state = city_data.get("state", "")
            city_timezone = city_data.get("time_zone", "")
            city_zip_code = city_data.get("zip_code", "")
            city_latitude = city_data.get("latitude")
            city_longitude = city_data.get("longitude")

            # Extract country data
            country_data = ip_data.get("country", {})
            country_code = country_data.get("code", "")
            country_name = country_data.get("name", "")
            country_continent = country_data.get("continent", "")

            # Update or create the IP record
            ip_record, created = DecodoIP.objects.update_or_create(
                ip_address=ip_address,
                defaults={
                    "ip_block": ip_block,
                    "port": port,
                    "isp_name": isp_name,
                    "isp_asn": isp_asn,
                    "isp_domain": isp_domain,
                    "isp_organization": isp_organization,
                    "city_name": city_name,
                    "city_code": city_code,
                    "city_state": city_state,
                    "city_timezone": city_timezone,
                    "city_zip_code": city_zip_code,
                    "city_latitude": city_latitude,
                    "city_longitude": city_longitude,
                    "country_code": country_code,
                    "country_name": country_name,
                    "country_continent": country_continent,
                    "updated_at": timezone.now()
                }
            )

            if created:
                logger.info("Created new IP record: %s (ISP: %s, Location: %s, %s)",
                           ip_address, isp_name, city_name, country_name)
            else:
                logger.info("Updated existing IP record: %s (ISP: %s, Location: %s, %s)",
                           ip_address, isp_name, city_name, country_name)

            # Create or update corresponding ProxyServer record
            _update_or_create_proxy_record(ip_record, ip_block)

            return created

        except Exception as e:
            logger.error("Error updating IP record: %s", str(e))
            return False


def _update_or_create_proxy_record(decodo_ip: DecodoIP, ip_block: DecodoIPBlock) -> bool:
    """
    Update or create a ProxyServer record for the given DecodoIP.
    
    Args:
        decodo_ip: The DecodoIP record to create a proxy for
        ip_block: The IP block containing proxy configuration
        
    Returns:
        True if a new record was created, False if existing record was updated
    """
    with traced("PROXY Update or Create Proxy Record", decodo_ip_id=str(decodo_ip.id), ip_block_id=str(ip_block.id)):
        try:
            # Use the port stored in the DecodoIP record
            # This is the actual port that was used to discover this IP
            port = decodo_ip.port

            # Generate a descriptive name
            location_parts = [decodo_ip.city_name, decodo_ip.city_state, decodo_ip.country_name]
            location = ", ".join([part for part in location_parts if part])
            proxy_name = f"Decodo {decodo_ip.ip_address}"
            if location:
                proxy_name += f" ({location})"

            # Check if proxy already exists to avoid overwriting is_active on update
            existing_proxy = ProxyServer.objects.filter(decodo_ip=decodo_ip).first()
            if not existing_proxy:
                existing_proxy = ProxyServer.objects.filter(
                    host=ip_block.endpoint,
                    port=port,
                ).first()

            defaults_dict = {
                "name": proxy_name,
                "proxy_type": ProxyServer.ProxyType.HTTPS,
                "host": ip_block.endpoint,
                "port": port,
                "username": ip_block.credential.username,
                "password": ip_block.credential.password,
                "static_ip": decodo_ip.ip_address,
                "is_dedicated": True,
                "notes": f"Auto-generated from Decodo IP block {ip_block.endpoint}:{ip_block.start_port}",
                "updated_at": timezone.now()
            }

            if existing_proxy:
                for field, value in defaults_dict.items():
                    setattr(existing_proxy, field, value)
                if existing_proxy.decodo_ip_id != decodo_ip.id:
                    existing_proxy.decodo_ip = decodo_ip
                existing_proxy.save()
                created = False
            else:
                defaults_dict["is_active"] = True
                proxy_server = ProxyServer.objects.create(
                    decodo_ip=decodo_ip,
                    **defaults_dict,
                )
                created = True

            if created:
                logger.info("Created new proxy record for IP: %s at %s:%d",
                           decodo_ip.ip_address, ip_block.endpoint, port)
            else:
                logger.info("Updated existing proxy record for IP: %s at %s:%d",
                           decodo_ip.ip_address, ip_block.endpoint, port)

            return created

        except Exception as e:
            logger.error("Error updating proxy record for IP %s: %s", decodo_ip.ip_address, str(e))
            return False


@shared_task(bind=True, ignore_result=True, name="gobii_platform.api.tasks.sync_all_ip_blocks")
def sync_all_ip_blocks(self) -> None:
    """
    Sync all Decodo IP blocks.

    This task is intended to be run periodically (e.g., daily) to keep
    all IP block data up to date.
    """
    with traced("PROXY Sync All IP Blocks"):
        try:
            ip_blocks = DecodoIPBlock.objects.all()
            logger.info("Starting sync for %d IP blocks", ip_blocks.count())

            for ip_block in ip_blocks:
                # Queue individual sync tasks for each block
                sync_ip_block.delay(str(ip_block.id))

            logger.info("Queued sync tasks for all IP blocks")

        except Exception as e:
            logger.exception("Error queuing sync tasks for all IP blocks: %s", str(e))


@shared_task(bind=True, ignore_result=True) 
def backfill_missing_proxy_records(self) -> None:
    """
    Create missing ProxyServer records for existing DecodoIP records.
    
    This task finds DecodoIP records that don't have associated ProxyServer
    records and creates them. This is useful for backfilling after adding
    the proxy integration or for fixing any data inconsistencies.
    """
    with traced("PROXY Backfill Missing Proxy Records"):
        try:
            # Find DecodoIP records without associated ProxyServer records
            missing_proxy_ips = DecodoIP.objects.filter(proxy_server__isnull=True).select_related('ip_block__credential')

            logger.info("Found %d DecodoIP records without proxy records", missing_proxy_ips.count())

            created_count = 0
            error_count = 0

            for decodo_ip in missing_proxy_ips:
                try:
                    was_created = _update_or_create_proxy_record(decodo_ip, decodo_ip.ip_block)
                    if was_created:
                        created_count += 1
                except Exception as e:
                    logger.error("Error creating proxy record for IP %s: %s", decodo_ip.ip_address, str(e))
                    error_count += 1

            logger.info("Backfill completed: %d proxy records created, %d errors", created_count, error_count)

        except Exception as e:
            logger.exception("Error during proxy record backfill: %s", str(e))


@shared_task(bind=True, ignore_result=True, name="gobii_platform.api.tasks.proxy_health_check_nightly")
def proxy_health_check_nightly(self):
    """
    Nightly health check for a random subsample of proxy servers.
    Selects a random subset of active proxies and performs health checks.
    Excludes proxies that have been tested in the past 48 hours.
    """
    import random
    with traced("PROXY Nightly Health Check"):
        logger.info("Starting nightly proxy health check")

        # Exclude proxies tested in the past 48 hours
        recent_cutoff = timezone.now() - timedelta(hours=48)

        # Get active proxies that haven't been tested recently
        recently_tested_proxy_ids = ProxyServer.objects.filter(
            is_active=True,
            health_check_results__checked_at__gte=recent_cutoff
        ).values_list('id', flat=True).distinct()

        active_proxies = ProxyServer.objects.filter(is_active=True).exclude(
            id__in=recently_tested_proxy_ids
        )

        total_active = ProxyServer.objects.filter(is_active=True).count()
        available_count = active_proxies.count()
        recently_tested_count = total_active - available_count

        logger.info(f"Found {total_active} total active proxies, {recently_tested_count} tested in past 48h, {available_count} available for testing")

        if available_count == 0:
            logger.info("No proxies available for health check (all tested recently)")
            return

        # Calculate sample size (20% of available proxies, min 50, max 1000)
        sample_size = max(50, min(1000, int(available_count * 0.2)))

        # If we don't have enough untested proxies, take what we can get
        actual_sample_size = min(sample_size, available_count)

        logger.info(f"Selecting {actual_sample_size} proxies from {available_count} available for health check")

        # Get random sample using order_by('?')
        proxy_sample = list(active_proxies.order_by('?')[:actual_sample_size])

        successful_checks = 0
        failed_checks = 0

        for proxy in proxy_sample:
            try:
                result = _perform_proxy_health_check(proxy)
                if not result:
                    failed_checks += 1
                    continue

                deactivated = proxy.record_health_check(result.passed)
                if result.passed:
                    successful_checks += 1
                else:
                    failed_checks += 1
                    if deactivated:
                        logger.warning(f"Proxy {proxy.host}:{proxy.port} auto-deactivated after {proxy.consecutive_health_failures} consecutive failures")
            except Exception as e:
                failed_checks += 1
                logger.error(f"Health check error for proxy {proxy.host}:{proxy.port}: {e}")

        logger.info(f"Health check complete. {successful_checks} passed, {failed_checks} failed")


@shared_task(bind=True, ignore_result=True)
def proxy_health_check_single(self, proxy_id: str):
    """
    Run a health check on a single proxy server.
    
    Args:
        proxy_id: UUID string of the ProxyServer to check
    """
    with traced("PROXY Single Health Check", proxy_id=proxy_id):
        try:
            proxy_server = ProxyServer.objects.get(id=proxy_id)
            logger.info(f"Starting on-demand health check for proxy {proxy_server.host}:{proxy_server.port}")

            result = _perform_proxy_health_check(proxy_server)

            if result:
                deactivated = proxy_server.record_health_check(result.passed)
                if deactivated:
                    logger.warning(f"Proxy {proxy_server.host}:{proxy_server.port} auto-deactivated after {proxy_server.consecutive_health_failures} consecutive failures")

        except ProxyServer.DoesNotExist:
            logger.error(f"Proxy server {proxy_id} not found for health check")
        except Exception as e:
            logger.error(f"Error during on-demand health check for proxy {proxy_id}: {e}")


def _perform_proxy_health_check(proxy_server: 'ProxyServer') -> 'ProxyHealthCheckResult':
    """
    Perform a health check on a single proxy server using browser automation.
    
    Args:
        proxy_server: ProxyServer instance to check
        
    Returns:
        ProxyHealthCheckResult: Result of the health check
    """
    from ..models import ProxyHealthCheckSpec, ProxyHealthCheckResult, BrowserUseAgentTask
    from django.contrib.auth import get_user_model
    from celery import current_task
    with traced("PROXY Perform Health Check", proxy_id=str(proxy_server.id)):
        logger.info(f"Performing health check for proxy {proxy_server.host}:{proxy_server.port}")

        start_time = time.time()

        try:
            # Get a random active health check spec
            spec = ProxyHealthCheckSpec.objects.filter(is_active=True).order_by('?').first()
            if not spec:
                logger.error("No active health check specs found")
                return ProxyHealthCheckResult.objects.create(
                    proxy_server=proxy_server,
                    health_check_spec=None,  # This will cause a FK constraint error - we need at least one spec
                    status=ProxyHealthCheckResult.Status.ERROR,
                    error_message="No active health check specs available"
                )



            # Hardcoded output schema for boolean health check results
            HEALTH_CHECK_OUTPUT_SCHEMA = {
                "type": "object",
                "properties": {
                    "result": {
                        "type": "boolean",
                        "description": "True if the health check passed, false if it failed"
                    }
                },
                "required": ["result"],
                "additionalProperties": False
            }

            # Create agentless browser use task for health check (no user required)
            task = BrowserUseAgentTask.objects.create(
                user=None,  # Health check task - no user required
                agent=None,  # Agentless task
                prompt=spec.prompt,
                output_schema=HEALTH_CHECK_OUTPUT_SCHEMA,
                status=BrowserUseAgentTask.StatusChoices.PENDING
            )

            logger.info(f"Created health check task {task.id} for proxy {proxy_server.host}:{proxy_server.port}")

            # Execute the health check task directly (avoid nested Celery tasks)
            task_start = time.time()

            try:
                # Process the task directly with timeout enforcement using signal
                def timeout_handler(signum, frame):
                    raise TimeoutError(f"Health check task {task.id} timed out after 120 seconds")

                # Set up timeout for the health check
                signal.signal(signal.SIGALRM, timeout_handler)
                signal.alarm(120)  # 2 minute timeout

                try:
                    # Import here to avoid circular imports
                    from .browser_agent_tasks import _process_browser_use_task_core

                    # Call the core task processor directly with proxy override
                    _process_browser_use_task_core(browser_use_agent_task_id=str(task.id), override_proxy_id=str(proxy_server.id))

                    # Cancel the timeout
                    signal.alarm(0)

                    # Refresh task from DB to get results
                    task.refresh_from_db()

                except TimeoutError:
                    signal.alarm(0)
                    logger.error(f"Health check task {task.id} timed out")
                    task.status = BrowserUseAgentTask.StatusChoices.FAILED
                    task.error_message = "Health check timed out after 120 seconds"
                    task.save()

            except Exception as e:
                signal.alarm(0)  # Ensure alarm is cancelled
                logger.error(f"Task execution failed for proxy {proxy_server.host}:{proxy_server.port}: {e}")
                task.status = BrowserUseAgentTask.StatusChoices.FAILED
                task.error_message = str(e)
                task.save()

            # Calculate response time
            response_time_ms = int((time.time() - start_time) * 1000)

            # Determine health check result based on task outcome
            logger.info(f"Health check task {task.id} completed with status: {task.status}")

            if task.status == BrowserUseAgentTask.StatusChoices.COMPLETED:
                # Get the latest task step result
                latest_step = task.steps.order_by('-created_at').first()
                logger.info(f"Health check task {task.id} - found latest step: {latest_step is not None}")

                if latest_step and latest_step.result_value:
                    try:
                        result_data = latest_step.result_value
                        logger.info(f"Health check task {task.id} - result_data: {result_data}, type: {type(result_data)}")

                        if isinstance(result_data, dict) and 'result' in result_data:
                            passed = bool(result_data['result'])
                            status = ProxyHealthCheckResult.Status.PASSED if passed else ProxyHealthCheckResult.Status.FAILED
                            logger.info(f"Health check task {task.id} - parsed result: {passed}, status: {status}")
                        else:
                            # Unexpected result format
                            status = ProxyHealthCheckResult.Status.ERROR
                            logger.warning(f"Unexpected result format from health check task {task.id}: {result_data}")
                    except Exception as e:
                        status = ProxyHealthCheckResult.Status.ERROR
                        logger.error(f"Error parsing health check result for task {task.id}: {e}")
                else:
                    status = ProxyHealthCheckResult.Status.ERROR
                    if latest_step:
                        logger.warning(f"Health check task {task.id} - latest step has no result_value: {latest_step.result_value}")
                    else:
                        logger.warning(f"No result step found for completed health check task {task.id}")
            elif task.status == BrowserUseAgentTask.StatusChoices.FAILED:
                status = ProxyHealthCheckResult.Status.FAILED
                logger.info(f"Health check task {task.id} failed with error: {task.error_message}")
            else:
                # Task is still pending or in progress - this shouldn't happen with synchronous execution
                status = ProxyHealthCheckResult.Status.TIMEOUT
                logger.warning(f"Health check task {task.id} in unexpected status: {task.status}")

            # Create and return the health check result
            result = ProxyHealthCheckResult.objects.create(
                proxy_server=proxy_server,
                health_check_spec=spec,
                status=status,
                response_time_ms=response_time_ms,
                error_message=task.error_message or "",
                task_result=latest_step.result_value if 'latest_step' in locals() and latest_step else None
            )

            logger.info(f"Health check completed for proxy {proxy_server.host}:{proxy_server.port} with status {status}")
            return result

        except Exception as e:
            response_time_ms = int((time.time() - start_time) * 1000)
            logger.error(f"Error during health check for {proxy_server.host}:{proxy_server.port}: {e}")

            # Try to create error result if we have a spec
            try:
                spec = ProxyHealthCheckSpec.objects.filter(is_active=True).first()
                return ProxyHealthCheckResult.objects.create(
                    proxy_server=proxy_server,
                    health_check_spec=spec,
                    status=ProxyHealthCheckResult.Status.ERROR,
                    response_time_ms=response_time_ms,
                    error_message=str(e)
                )
            except Exception as inner_e:
                logger.error(f"Failed to create error result for proxy {proxy_server.host}:{proxy_server.port}: {inner_e}")
                raise e
