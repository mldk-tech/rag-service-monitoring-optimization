"""Health monitoring module for comprehensive system health checks."""

from typing import Dict, Any, Callable, Optional, List
import asyncio
import time
import logging
from dataclasses import dataclass
from datetime import datetime
from prometheus_client import Gauge

@dataclass
class HealthStatus:
    """Health status data structure."""
    healthy: bool
    message: str
    timestamp: datetime
    details: Dict[str, Any]

class HealthCheck:
    """Individual health check implementation."""

    def __init__(
        self,
        name: str,
        check_function: Callable[[], Dict[str, Any]],
        interval: int,
        timeout: int
    ):
        """Initialize health check.
        
        Args:
            name: Health check name
            check_function: Function to perform health check
            interval: Check interval in seconds
            timeout: Check timeout in seconds
        """
        self.name = name
        self.check_function = check_function
        self.interval = interval
        self.timeout = timeout
        self.last_status: Optional[HealthStatus] = None
        self.consecutive_failures = 0

        # Metrics
        self.health_status = Gauge(
            f'rag_health_{name}_status',
            f'Health status for {name}',
            ['component']
        )
        self.check_duration = Gauge(
            f'rag_health_{name}_check_duration_seconds',
            f'Health check duration for {name}',
            ['component']
        )

    async def run_check(self) -> HealthStatus:
        """Run health check with timeout.
        
        Returns:
            HealthStatus object with check results
        """
        start_time = time.time()
        try:
            async with asyncio.timeout(self.timeout):
                result = await asyncio.to_thread(self.check_function)
                duration = time.time() - start_time
                
                status = HealthStatus(
                    healthy=True,
                    message="Health check passed",
                    timestamp=datetime.utcnow(),
                    details=result
                )
                
                # Update metrics
                self.health_status.labels(component=self.name).set(1)
                self.check_duration.labels(component=self.name).set(duration)
                
                self.consecutive_failures = 0
                self.last_status = status
                return status

        except asyncio.TimeoutError:
            duration = time.time() - start_time
            status = HealthStatus(
                healthy=False,
                message=f"Health check timed out after {self.timeout}s",
                timestamp=datetime.utcnow(),
                details={"error": "timeout"}
            )
            
            # Update metrics
            self.health_status.labels(component=self.name).set(0)
            self.check_duration.labels(component=self.name).set(duration)
            
            self.consecutive_failures += 1
            self.last_status = status
            return status

        except Exception as e:
            duration = time.time() - start_time
            status = HealthStatus(
                healthy=False,
                message=f"Health check failed: {str(e)}",
                timestamp=datetime.utcnow(),
                details={"error": str(e)}
            )
            
            # Update metrics
            self.health_status.labels(component=self.name).set(0)
            self.check_duration.labels(component=self.name).set(duration)
            
            self.consecutive_failures += 1
            self.last_status = status
            return status

class AlertManager:
    """Alert management for health check failures."""

    def __init__(
        self,
        alert_endpoints: List[Dict[str, str]],
        min_failures: int = 3
    ):
        """Initialize alert manager.
        
        Args:
            alert_endpoints: List of alert endpoint configurations
            min_failures: Minimum consecutive failures before alerting
        """
        self.alert_endpoints = alert_endpoints
        self.min_failures = min_failures
        
        # Metrics
        self.alerts_sent = Gauge(
            'rag_health_alerts_sent_total',
            'Total number of health check alerts sent',
            ['component', 'severity']
        )

    async def send_alert(
        self,
        component: str,
        status: HealthStatus,
        consecutive_failures: int
    ) -> None:
        """Send alert for health check failure.
        
        Args:
            component: Component name
            status: Health status
            consecutive_failures: Number of consecutive failures
        """
        if consecutive_failures < self.min_failures:
            return

        severity = "critical" if consecutive_failures >= 2 * self.min_failures else "warning"
        
        alert_message = {
            "component": component,
            "status": "unhealthy",
            "message": status.message,
            "consecutive_failures": consecutive_failures,
            "last_check_time": status.timestamp.isoformat(),
            "details": status.details,
            "severity": severity
        }

        for endpoint in self.alert_endpoints:
            try:
                if endpoint["type"] == "slack":
                    await self._send_slack_alert(endpoint["url"], alert_message)
                elif endpoint["type"] == "email":
                    await self._send_email_alert(endpoint["address"], alert_message)
                
                # Update metrics
                self.alerts_sent.labels(
                    component=component,
                    severity=severity
                ).inc()
                
            except Exception as e:
                logging.error(
                    "Failed to send alert to %s: %s",
                    endpoint["type"],
                    str(e)
                )

    async def _send_slack_alert(
        self,
        webhook_url: str,
        message: Dict[str, Any]
    ) -> None:
        """Send alert to Slack.
        
        Args:
            webhook_url: Slack webhook URL
            message: Alert message
        """
        # Placeholder - implement actual Slack integration
        logging.info("Sending Slack alert: %s", message)

    async def _send_email_alert(
        self,
        address: str,
        message: Dict[str, Any]
    ) -> None:
        """Send alert via email.
        
        Args:
            address: Email address
            message: Alert message
        """
        # Placeholder - implement actual email integration
        logging.info("Sending email alert to %s: %s", address, message)

class HealthMonitor:
    """System health monitoring with periodic checks and alerting."""

    def __init__(
        self,
        check_interval: int = 60,
        alert_manager: Optional[AlertManager] = None
    ):
        """Initialize health monitor.
        
        Args:
            check_interval: Default check interval in seconds
            alert_manager: Alert manager instance
        """
        self.check_interval = check_interval
        self.alert_manager = alert_manager
        self.checks: Dict[str, HealthCheck] = {}
        self.running = False
        
        # Metrics
        self.overall_health = Gauge(
            'rag_health_overall_status',
            'Overall system health status'
        )

    def add_check(
        self,
        name: str,
        check_function: Callable[[], Dict[str, Any]],
        interval: Optional[int] = None,
        timeout: int = 30
    ) -> None:
        """Add health check.
        
        Args:
            name: Check name
            check_function: Function to perform check
            interval: Check interval in seconds (optional)
            timeout: Check timeout in seconds
        """
        check = HealthCheck(
            name=name,
            check_function=check_function,
            interval=interval or self.check_interval,
            timeout=timeout
        )
        self.checks[name] = check
        logging.info("Added health check: %s", name)

    async def start(self) -> None:
        """Start health monitoring."""
        self.running = True
        tasks = []
        
        for check in self.checks.values():
            task = asyncio.create_task(self._run_check_loop(check))
            tasks.append(task)
        
        logging.info("Health monitoring started with %d checks", len(tasks))
        await asyncio.gather(*tasks)

    async def stop(self) -> None:
        """Stop health monitoring."""
        self.running = False
        logging.info("Health monitoring stopped")

    async def _run_check_loop(self, check: HealthCheck) -> None:
        """Run continuous health check loop.
        
        Args:
            check: Health check to run
        """
        while self.running:
            status = await check.run_check()
            
            if not status.healthy and self.alert_manager:
                await self.alert_manager.send_alert(
                    component=check.name,
                    status=status,
                    consecutive_failures=check.consecutive_failures
                )
            
            # Update overall health status
            all_healthy = all(
                c.last_status and c.last_status.healthy
                for c in self.checks.values()
            )
            self.overall_health.set(1 if all_healthy else 0)
            
            await asyncio.sleep(check.interval)

    async def check_health(self) -> Dict[str, Any]:
        """Run all health checks once.
        
        Returns:
            Dictionary containing all health check results
        """
        results = {}
        overall_healthy = True
        
        for name, check in self.checks.items():
            status = await check.run_check()
            results[name] = {
                "healthy": status.healthy,
                "message": status.message,
                "timestamp": status.timestamp.isoformat(),
                "details": status.details
            }
            if not status.healthy:
                overall_healthy = False
        
        results["overall_healthy"] = overall_healthy
        self.overall_health.set(1 if overall_healthy else 0)
        
        return results 