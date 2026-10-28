from services.api.app.provider_accounting import Ticket


def test_failed_unlock_invalidates_the_session_instead_of_pooling_a_live_lock():
    class Lease:
        invalidated = False
        closed = False

        def execute(self, statement):
            raise RuntimeError("sensitive driver fixture failure")

        def invalidate(self):
            self.invalidated = True

        def close(self):
            self.closed = True

    ticket = Ticket(None, "unused", None)
    lease = Lease()
    ticket.lease = lease
    ticket.release()
    ticket.release()
    assert lease.invalidated and lease.closed and ticket.lease is None
