import threading
import time
import unittest

from zinekit.preview import LatestWorker, PreviewRenderer, downscale
from tests import helpers


class WorkerTest(unittest.TestCase):
    def test_skips_to_latest(self):
        gate = threading.Event()
        ran, done = [], []

        def fn(job):
            gate.wait(2)
            ran.append(job)
            return job * 10
        w = LatestWorker(fn, lambda g, r, e, s: done.append((g, r, e)))
        w.submit(1)
        time.sleep(0.05)          # job 1 is running
        for j in range(2, 10):
            w.submit(j)
        self.assertTrue(w.busy)
        gate.set()
        for _ in range(100):
            if len(done) == 2:
                break
            time.sleep(0.02)
        w.close()
        self.assertEqual(ran, [1, 9])
        self.assertEqual([d[1] for d in done], [10, 90])
        self.assertEqual(done[-1][0], 9)
        self.assertFalse(w.busy)

    def test_errors_are_reported(self):
        done = []
        w = LatestWorker(lambda j: 1 / j, lambda g, r, e, s: done.append(e))
        w.submit(0)
        w.submit(0)
        for _ in range(100):
            if done:
                break
            time.sleep(0.02)
        w.submit(1)
        for _ in range(100):
            if len(done) >= 2 and done[-1] is None:
                break
            time.sleep(0.02)
        w.close()
        self.assertIsInstance(done[0], ZeroDivisionError)
        self.assertIsNone(done[-1])


class RendererTest(unittest.TestCase):
    def test_render_and_cache(self):
        r = PreviewRenderer(keep=2)
        a = helpers.photo(160, 90)
        out1 = r.render(a, {})
        r.render(helpers.photo(80, 45), {})
        r.render(helpers.photo(40, 22), {})
        self.assertEqual(len(r._renderers), 2)
        self.assertEqual(r.render(a, {}).tobytes(), out1.tobytes())
        r.close()

    def test_downscale(self):
        img = helpers.photo(640, 360)
        self.assertEqual(downscale(img, 180).size, (320, 180))
        self.assertIs(downscale(img, 0), img)
        self.assertIs(downscale(img, 1000), img)


if __name__ == "__main__":
    unittest.main()
