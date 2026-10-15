"""States module (sections 11-14).

The States module is the user's control surface and is composed from data that
already has an owner, rather than duplicating it:

* **Settings**   - :mod:`personalized_agent.common.config` (schema driven) rendered by
  ``/api/settings`` and the Settings panel in ``api/web``;
* **Graph display** - :class:`personalized_agent.data_manage.graph.store.GraphStore`
  neighbourhood queries rendered by ``/api/graph`` and the Graph panel;
* **Debugger**   - :class:`personalized_agent.common.observability.metrics.Observability`
  rendered by ``/api/debugger`` and the Debugger panel.

The desktop agent opens these panels from its right click menu (section 7.3).
"""
