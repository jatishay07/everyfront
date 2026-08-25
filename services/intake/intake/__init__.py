"""Gmail intake -- RELAY (persona 4) WO1.

    Gmail `users.watch` --push--> Pub/Sub `intake.email.received`
        --push subscription--> this service
        --> fetch message, store attachments to GCS
        --> publish `case.document.added`

Everything that talks to an external system (Gmail, GCS, Pub/Sub) is a thin
wrapper here so `main.py`'s route handlers stay readable; see each module's
docstring for the one thing it owns.
"""
