"""Unit tests for Dijkstra and Bellman-Ford routing algorithms.

No Mininet / sudo needed.
"""
import logging
import os
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def build_app(graph):
    with patch('os_ken.base.app_manager.OSKenApp.__init__', return_value=None):
        from controller import ControllerApp

    app = ControllerApp()
    app.logger = logging.getLogger('test')
    app.graph = graph
    return app


class TestShortestPath(unittest.TestCase):

    def setUp(self):
        self.triangle = {
            1: {2: 12, 3: 13},
            2: {1: 21, 3: 23},
            3: {1: 31, 2: 32},
        }
        self.line = {
            10: {20: 1020},
            20: {10: 2010, 30: 2030},
            30: {20: 3020, 40: 3040},
            40: {30: 4030},
        }
        self.disconnected = {
            1: {2: 12},
            2: {1: 21},
            3: {},
        }

    def _assert_path_valid(self, graph, path, src, dst):
        if src == dst:
            self.assertEqual(path, [], 'Same node should return empty path')
            return

        if path is None:
            return

        self.assertGreater(len(path), 0,
                           'Path must be non-empty for different nodes')

        current = src
        for dpid, out_port in path:
            self.assertEqual(dpid, current,
                             'Each hop dpid must match current switch')
            self.assertIn(dpid, graph,
                          'Hop dpid must be in graph')
            self.assertIn(out_port, graph[dpid].values(),
                          'Out port must exist on this switch')
            next_dpid = None
            for v, p in graph[dpid].items():
                if p == out_port:
                    next_dpid = v
                    break
            self.assertIsNotNone(next_dpid,
                                 'Out port must lead to a known neighbor')
            current = next_dpid

        self.assertEqual(current, dst,
                         'Path must end at destination')

    def _test_graph(self, graph, name):
        app = build_app(graph)
        nodes = sorted(graph.keys())

        for src in nodes:
            for dst in nodes:
                dj_path = app._dijkstra(src, dst)
                bf_path = app._bellman_ford(src, dst)

                self.assertEqual(dj_path, bf_path,
                                 f'{name}: Dijkstra and Bellman-Ford differ: '
                                 f'src={src} dst={dst}\n'
                                 f'  Dijkstra:    {dj_path}\n'
                                 f'  BellmanFord: {bf_path}')

                self._assert_path_valid(graph, dj_path, src, dst)
                self._assert_path_valid(graph, bf_path, src, dst)

    def test_triangle_topology(self):
        self._test_graph(self.triangle, 'triangle')

    def test_line_topology(self):
        self._test_graph(self.line, 'line')

    def test_disconnected_graph(self):
        app = build_app(self.disconnected)
        self.assertIsNone(app._bellman_ford(1, 3),
                          'No path should exist between disconnected nodes')
        self.assertIsNone(app._dijkstra(1, 3),
                          'No path should exist between disconnected nodes')

    def test_missing_nodes(self):
        app = build_app(self.triangle)
        self.assertIsNone(app._bellman_ford(1, 99),
                          'Should return None for node not in graph')
        self.assertIsNone(app._dijkstra(1, 99),
                          'Should return None for node not in graph')

    def test_same_node(self):
        app = build_app(self.triangle)
        for node in self.triangle:
            self.assertEqual(app._bellman_ford(node, node), [],
                             'Same node should return empty list')
            self.assertEqual(app._dijkstra(node, node), [],
                             'Same node should return empty list')

    def test_single_edge(self):
        graph = {1: {2: 12}, 2: {1: 21}}
        app = build_app(graph)
        path = app._bellman_ford(1, 2)
        self.assertEqual(path, [(1, 12)], 'Direct edge must be used')
        path = app._dijkstra(1, 2)
        self.assertEqual(path, [(1, 12)], 'Direct edge must be used')

    def test_routing_algorithm_dispatch(self):
        app = build_app(self.triangle)
        app.ROUTING_ALGORITHM = 'bellman_ford'
        path = app._shortest_path(1, 2)
        bf = app._bellman_ford(1, 2)
        self.assertEqual(path, bf)

        app.ROUTING_ALGORITHM = 'dijkstra'
        path = app._shortest_path(1, 2)
        dj = app._dijkstra(1, 2)
        self.assertEqual(path, dj)


if __name__ == '__main__':
    unittest.main()
