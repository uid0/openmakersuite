"""URL routing for the interlocks API.

Registered at the router root so the viewset lives directly under
``/api/interlocks/`` (matching the print-queue-style flat layout):

* ``/api/interlocks/``                          list / create
* ``/api/interlocks/{id}/``                      retrieve / update / delete
* ``/api/interlocks/{id}/enable|disable|status/`` operator actions
* ``/api/interlocks/command-queue/``             Pi executor poll (daemon token)
* ``/api/interlocks/commands/{id}/report/``      Pi executor result ingest
"""

from rest_framework.routers import DefaultRouter

from .views import InterlockViewSet

router = DefaultRouter()
router.register(r"", InterlockViewSet, basename="interlock")

urlpatterns = router.urls
