# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 VyapaarOS Contributors
#
# This file is an original VyapaarOS addition to a work derived from
# NVIDIA-AI-Blueprints/Retail-Agentic-Commerce (Apache-2.0).
# See NOTICE and VYAPAAR_ATTRIBUTION.md at the repository root.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Deterministic money-state control plane for VyapaarOS.

This package owns every state transition that money can undergo. No LLM,
agent tool, or protocol adapter may write payment state directly; they must
route through :mod:`state_machine` so that transitions are validated, and
through :mod:`policy` so that value limits are enforced.
"""
