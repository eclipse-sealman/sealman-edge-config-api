"""One dispatch module per extension upstream `type` discriminator.

Empty package marker only. `runtime.py` imports each module (`http`, `iotedge`)
directly rather than re-exporting through here. A future third upstream type is
added the same way: one new file named after its `type` string, nothing else
in the package needs restructuring.
"""
