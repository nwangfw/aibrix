/*
Copyright 2025 The Aibrix Team.

Licensed under the Apache License, Version 2.0 (the "License");
you may not use this file except in compliance with the License.
You may obtain a copy of the License at

    http://www.apache.org/licenses/LICENSE-2.0

Unless required by applicable law or agreed to in writing, software
distributed under the License is distributed on an "AS IS" BASIS,
WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
See the License for the specific language governing permissions and
limitations under the License.
*/

// Package planner is the AIBrix scheduling core.
//
// The Scheduler accepts a user-submitted PlannerJob, reserves capacity from
// the Resource Manager (RM), and submits the resulting workload to the
// Metadata Service (MDS) as an OpenAI-format batch. On MDS-submission failure
// it rolls back the RM reservation so capacity is not held indefinitely.
//
// External collaborators (RM, MDS) are accessed through the RMClient and
// MDSSubmitter interfaces. Callers inject HTTP-backed implementations in
// production and in-memory fakes in tests. This package ships baseline
// in-memory implementations (InMemoryRMClient, LoggingMDSSubmitter) so the
// console binary can boot and accept traffic before real backends exist.
package planner
