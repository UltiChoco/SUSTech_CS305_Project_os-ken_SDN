"""Controller entrypoint with Bellman-Ford routing algorithm.

Inherits all behaviour from the main controller, only changing the routing
algorithm to Bellman-Ford for testing purposes.
"""
from controller import ControllerApp


class BellmanFordControllerApp(ControllerApp):
    OFP_VERSIONS = ControllerApp.OFP_VERSIONS
    FORWARDING_COOKIE = ControllerApp.FORWARDING_COOKIE
    FORWARDING_PRIORITY = ControllerApp.FORWARDING_PRIORITY
    ROUTING_ALGORITHM = "bellman_ford"


if __name__ == '__main__':
    from os_ken.base.app_manager import main
    main()
