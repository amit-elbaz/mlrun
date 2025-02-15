# Copyright 2023 Iguazio
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#   http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import asyncio
import datetime
import typing

from kubernetes.client import ApiException

import mlrun.common.schemas
import mlrun.errors
import mlrun.model
import mlrun.utils.helpers
import mlrun.utils.notifications.notification as notification_module
import mlrun.utils.notifications.notification.base as base
from mlrun.utils import logger
from mlrun.utils.notifications.notification_pusher import (
    NotificationPusher,
    _NotificationPusherBase,
)

import framework.api.utils
import framework.constants
import framework.utils.singletons.k8s


class RunNotificationPusher(NotificationPusher):
    mail_notification_default_params = None

    @staticmethod
    def resolve_notifications_default_params():
        # TODO: After implementing make running notification send from the server side (ML-8069),
        #       we should move all the notifications classes from the client to the server and also
        #       create new function on the NotificationBase class for resolving the default params.
        #       After that we can remove this function.
        return {
            notification_module.NotificationTypes.console: {},
            notification_module.NotificationTypes.git: {},
            notification_module.NotificationTypes.ipython: {},
            notification_module.NotificationTypes.slack: {},
            notification_module.NotificationTypes.mail: RunNotificationPusher.get_mail_notification_default_params(),
            notification_module.NotificationTypes.webhook: {},
        }

    @staticmethod
    def get_mail_notification_default_params(refresh=False):
        if (
            not refresh
            and RunNotificationPusher.mail_notification_default_params is not None
        ):
            return RunNotificationPusher.mail_notification_default_params

        mail_notification_default_params = (
            RunNotificationPusher._get_mail_notifications_default_params_from_secret()
        )

        RunNotificationPusher.mail_notification_default_params = (
            mail_notification_default_params
        )
        return RunNotificationPusher.mail_notification_default_params

    @staticmethod
    def _get_mail_notifications_default_params_from_secret():
        smtp_config_secret_name = mlrun.mlconf.notifications.smtp.config_secret_name
        mail_notification_default_params = {}
        if framework.utils.singletons.k8s.get_k8s_helper().is_running_inside_kubernetes_cluster():
            try:
                mail_notification_default_params = (
                    framework.utils.singletons.k8s.get_k8s_helper().read_secret_data(
                        smtp_config_secret_name, load_as_json=True, silent=True
                    )
                ) or {}
            except ApiException as exc:
                logger.warning(
                    "Failed to read SMTP configuration secret",
                    secret_name=smtp_config_secret_name,
                    body=mlrun.errors.err_to_str(exc.body),
                )
        return mail_notification_default_params

    def _prepare_notification_args(
        self, run: mlrun.model.RunObject, notification_object: mlrun.model.Notification
    ):
        """
        Prepare notification arguments for the notification pusher.
        In the server side implementation, we need to mask the notification parameters on the task as they are
        unmasked to extract the credentials required to send the notification.
        """
        message, severity, runs = super()._prepare_notification_args(
            run, notification_object
        )
        for run in runs:
            framework.utils.notifications.mask_notification_params_on_task(
                run, framework.constants.MaskOperations.REDACT
            )

        return message, severity, runs


class AlertNotificationPusher(_NotificationPusherBase):
    def push(
        self,
        alert: mlrun.common.schemas.AlertConfig,
        event_data: mlrun.common.schemas.Event,
    ):
        """
        Asynchronously push notification.
        """

        def sync_push():
            pass

        async def async_push():
            tasks = []
            for notification_data in alert.notifications:
                notification_object = mlrun.model.Notification.from_dict(
                    notification_data.notification.dict()
                )

                notification_object = (
                    framework.utils.notifications.unmask_notification_params_secret(
                        alert.project, notification_object
                    )
                )

                name = notification_object.name
                notification_type = notification_module.NotificationTypes(
                    notification_object.kind
                )
                params = {}
                if notification_object.secret_params:
                    params.update(notification_object.secret_params)
                if notification_object.params:
                    params.update(notification_object.params)
                notification = notification_type.get_notification()(name, params)

                tasks.append(
                    self._push_notification_async(
                        notification,
                        alert,
                        notification_data.notification,
                        event_data,
                    )
                )

            # return exceptions to "best-effort" fire all notifications
            results = await asyncio.gather(*tasks, return_exceptions=True)
            for result in results:
                if isinstance(result, Exception):
                    logger.warning(
                        "Failed to push notification async",
                        error=mlrun.errors.err_to_str(result),
                    )

        self._push(sync_push, async_push)

    async def _push_notification_async(
        self,
        notification: base.NotificationBase,
        alert: mlrun.common.schemas.AlertConfig,
        notification_object: mlrun.common.schemas.Notification,
        event_data: mlrun.common.schemas.Event,
    ):
        message, severity = self._prepare_notification_args(alert, notification_object)
        logger.debug(
            "Pushing async notification",
            notification=notification_object,
            name=alert.name,
        )
        try:
            await notification.push(
                message, severity, alert=alert, event_data=event_data
            )
            logger.debug(
                "Notification sent successfully",
                notification=notification_object,
                name=alert.name,
            )
            await mlrun.utils.helpers.run_in_threadpool(
                self._update_notification_status,
                alert.id,
                alert.project,
                notification_object,
                status=mlrun.common.schemas.NotificationStatus.SENT,
                sent_time=datetime.datetime.now(tz=datetime.timezone.utc),
            )
        except Exception as exc:
            logger.warning(
                "Failed to send notification",
                notification=notification_object,
                name=alert.name,
                exc=mlrun.errors.err_to_str(exc),
            )
            await mlrun.utils.helpers.run_in_threadpool(
                self._update_notification_status,
                alert.id,
                alert.project,
                notification_object,
                status=mlrun.common.schemas.NotificationStatus.ERROR,
                reason=str(exc),
            )
            raise exc

    @staticmethod
    def _prepare_notification_args(
        alert: mlrun.common.schemas.AlertConfig,
        notification_object: mlrun.common.schemas.Notification,
    ):
        message = (
            f": {notification_object.message}"
            if notification_object.message
            else alert.summary
        )

        severity = alert.severity
        return message, severity

    @staticmethod
    def _update_notification_status(
        alert_id: int,
        project: str,
        notification: mlrun.common.schemas.Notification,
        status: typing.Optional[str] = None,
        sent_time: typing.Optional[datetime.datetime] = None,
        reason: typing.Optional[str] = None,
    ):
        db = mlrun.get_run_db()
        notification.status = status or notification.status
        notification.sent_time = sent_time or notification.sent_time

        # fill reason only if failed
        if notification.status == mlrun.common.schemas.NotificationStatus.ERROR:
            notification.reason = reason or notification.reason

            # limit reason to a max of 255 characters (for db reasons) but also for human readability reasons.
            notification.reason = notification.reason[:255]
        else:
            notification.reason = None

        # There is no need to mask the params as the secrets are already loaded
        db.store_alert_notifications(
            None,
            [notification],
            alert_id,
            project,
            mask_params=False,
        )
