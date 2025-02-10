import io
import json
import math
import os
import sys
import unittest

from .solver import MelangeSolver


class TestMelangeSolver(unittest.TestCase):
    def setUp(self):
        self.config_path = os.path.join(
            os.path.dirname(__file__), "config_example_with_constraints.json"
        )

    def test_solver_with_gpu_constraints(self):
        # Test case 1: With GPU constraints
        with open(self.config_path, "r") as f:
            config = json.load(f)

        solver = MelangeSolver(
            workload_distribution=config["workload_distribution"],
            total_request_rate=config["total_request_rate"],
            gpu_info=config["gpu_info"],
            slice_factor=config["slice_factor"],
        )

        original_stdout = sys.stdout
        captured_output = io.StringIO()
        sys.stdout = captured_output

        result = solver.run(logs=True)

        sys.stdout = original_stdout
        solver_output = captured_output.getvalue()

        # Extract the decision matrix from the solver's output
        decision_matrix = []
        capture_matrix = False
        for line in solver_output.split("\n"):
            if line.strip() == "Decision Matrix:":
                capture_matrix = True
                continue
            elif line.strip() == "Decision Vector:":
                capture_matrix = False
                break

            if capture_matrix and line.strip():
                row = [float(val) for val in line.strip("[]").split(",")]
                decision_matrix.append(row)

        print("\nGPU constraints test results:")
        print(f"GPU allocation: {result}")
        print("Decision Matrix (extracted):")
        for row in decision_matrix:
            print(row)

        # Calculate total workload
        total_workload = (
            sum(sum(row) for row in config["workload_distribution"])
            * config["total_request_rate"]
        )
        print(f"Total workload: {total_workload}")
        print(
            f"A10G count: {result['A10G']} (max: {config['gpu_info']['A10G']['max_count']})"
        )
        print(
            f"A100-80GB count: {result['A100-80GB']} (max: {config['gpu_info']['A100-80GB']['max_count']})"
        )

        # Print detailed workload distribution
        print("\nWorkload distribution matrix:")
        for i, row in enumerate(config["workload_distribution"]):
            print(f"Row {i}: {row}")

        # Calculate workload assignment based on the decision matrix
        print("\nWorkload assignment based on decision matrix:")

        # Reconstruct the slices from the workload distribution
        slices = []
        slice_indices = []  # Keep track of which cell each slice belongs to
        for i in range(len(config["workload_distribution"])):
            for j in range(len(config["workload_distribution"][i])):
                cell_workload = (
                    config["workload_distribution"][i][j] * config["total_request_rate"]
                )
                for _ in range(config["slice_factor"]):
                    slices.append(cell_workload / config["slice_factor"])
                    slice_indices.append((i, j))

        # Calculate workload assigned to each GPU type for each cell
        a10g_workload_by_cell = {}
        a100_workload_by_cell = {}

        for slice_idx, (i, j) in enumerate(slice_indices):
            if slice_idx < len(decision_matrix):
                a10g_assignment = decision_matrix[slice_idx][0]
                a100_assignment = decision_matrix[slice_idx][1]

                cell_key = (i, j)
                a10g_workload_by_cell[cell_key] = (
                    a10g_workload_by_cell.get(cell_key, 0)
                    + slices[slice_idx] * a10g_assignment
                )
                a100_workload_by_cell[cell_key] = (
                    a100_workload_by_cell.get(cell_key, 0)
                    + slices[slice_idx] * a100_assignment
                )

        # Calculate load for each GPU type based on assigned workload
        a10g_load = 0
        a100_load = 0

        for i in range(len(config["workload_distribution"])):
            for j in range(len(config["workload_distribution"][i])):
                cell_key = (i, j)
                a10g_cell_workload = a10g_workload_by_cell.get(cell_key, 0)
                a100_cell_workload = a100_workload_by_cell.get(cell_key, 0)

                a10g_throughput = config["gpu_info"]["A10G"]["tputs"][i][j]
                a10g_cell_load = (
                    a10g_cell_workload / a10g_throughput if a10g_throughput > 0 else 0
                )
                a10g_load += a10g_cell_load

                a100_throughput = config["gpu_info"]["A100-80GB"]["tputs"][i][j]
                a100_cell_load = (
                    a100_cell_workload / a100_throughput if a100_throughput > 0 else 0
                )
                a100_load += a100_cell_load

                total_cell_workload = (
                    config["workload_distribution"][i][j] * config["total_request_rate"]
                )
                print(
                    f"Cell [{i}][{j}]: Total workload = {total_cell_workload:.1f} requests"
                )
                print(
                    f"  - A10G: Assigned workload = {a10g_cell_workload:.1f} ({a10g_cell_workload/total_cell_workload*100:.1f}%), "
                    + f"Throughput = {a10g_throughput:.1f}, Load = {a10g_cell_load:.3f}"
                )
                print(
                    f"  - A100-80GB: Assigned workload = {a100_cell_workload:.1f} ({a100_cell_workload/total_cell_workload*100:.1f}%), "
                    + f"Throughput = {a100_throughput:.1f}, Load = {a100_cell_load:.3f}"
                )

        print(f"\nTotal A10G load: {a10g_load:.3f} GPUs (allocated: {result['A10G']})")
        print(
            f"Total A100-80GB load: {a100_load:.3f} GPUs (allocated: {result['A100-80GB']})"
        )

        # Verify cost calculation
        expected_cost = (
            result["A10G"] * config["gpu_info"]["A10G"]["cost"]
            + result["A100-80GB"] * config["gpu_info"]["A100-80GB"]["cost"]
        )
        self.assertEqual(
            result["cost"],
            expected_cost,
            "Cost calculation should match expected value",
        )
        print("--------------------------------")

    def test_solver_without_gpu_constraints(self):
        # Test case 2: Without GPU constraints (should use default large number)
        with open(self.config_path, "r") as f:
            config = json.load(f)

        # Remove max_count constraints
        for gpu in config["gpu_info"]:
            del config["gpu_info"][gpu]["max_count"]

        solver = MelangeSolver(
            workload_distribution=config["workload_distribution"],
            total_request_rate=config["total_request_rate"],
            gpu_info=config["gpu_info"],
            slice_factor=config["slice_factor"],
        )

        # Capture the solver's output to extract the decision matrix
        import io
        import sys

        original_stdout = sys.stdout
        captured_output = io.StringIO()
        sys.stdout = captured_output

        result = solver.run(logs=True)

        sys.stdout = original_stdout
        solver_output = captured_output.getvalue()

        # Extract the decision matrix from the solver's output: Don't want to update the solver to return the decision matrix since it will change the output interface
        decision_matrix = []
        capture_matrix = False
        for line in solver_output.split("\n"):
            if line.strip() == "Decision Matrix:":
                capture_matrix = True
                continue
            elif line.strip() == "Decision Vector:":
                capture_matrix = False
                break

            if capture_matrix and line.strip():
                row = [float(val) for val in line.strip("[]").split(",")]
                decision_matrix.append(row)

        self.assertIsNotNone(result)

        print("\nNo GPU constraints test results:")
        print(f"GPU allocation: {result}")
        print("Decision Matrix (extracted):")
        for row in decision_matrix:
            print(row)

        # Calculate total workload
        total_workload = (
            sum(sum(row) for row in config["workload_distribution"])
            * config["total_request_rate"]
        )
        print(f"Total workload: {total_workload}")
        print(f"A10G count: {result['A10G']}")
        print(f"A100-80GB count: {result['A100-80GB']}")

        # Print detailed workload distribution
        print("\nWorkload distribution matrix:")
        for i, row in enumerate(config["workload_distribution"]):
            print(f"Row {i}: {row}")

        # Calculate workload assignment based on the decision matrix
        print("\nWorkload assignment based on decision matrix:")

        # Reconstruct the slices from the workload distribution
        slices = []
        slice_indices = []  # Keep track of which cell each slice belongs to
        for i in range(len(config["workload_distribution"])):
            for j in range(len(config["workload_distribution"][i])):
                cell_workload = (
                    config["workload_distribution"][i][j] * config["total_request_rate"]
                )
                for _ in range(config["slice_factor"]):
                    slices.append(cell_workload / config["slice_factor"])
                    slice_indices.append((i, j))

        # Calculate workload assigned to each GPU type for each cell
        a10g_workload_by_cell = {}
        a100_workload_by_cell = {}

        for slice_idx, (i, j) in enumerate(slice_indices):
            if slice_idx < len(decision_matrix):
                # decision_matrix[slice_idx][0] is the assignment to A10G
                # decision_matrix[slice_idx][1] is the assignment to A100-80GB
                a10g_assignment = decision_matrix[slice_idx][0]
                a100_assignment = decision_matrix[slice_idx][1]

                cell_key = (i, j)
                a10g_workload_by_cell[cell_key] = (
                    a10g_workload_by_cell.get(cell_key, 0)
                    + slices[slice_idx] * a10g_assignment
                )
                a100_workload_by_cell[cell_key] = (
                    a100_workload_by_cell.get(cell_key, 0)
                    + slices[slice_idx] * a100_assignment
                )

        # Calculate load for each GPU type based on assigned workload
        a10g_load = 0
        a100_load = 0

        for i in range(len(config["workload_distribution"])):
            for j in range(len(config["workload_distribution"][i])):
                cell_key = (i, j)
                a10g_cell_workload = a10g_workload_by_cell.get(cell_key, 0)
                a100_cell_workload = a100_workload_by_cell.get(cell_key, 0)

                a10g_throughput = config["gpu_info"]["A10G"]["tputs"][i][j]
                a10g_cell_load = (
                    a10g_cell_workload / a10g_throughput if a10g_throughput > 0 else 0
                )
                a10g_load += a10g_cell_load

                a100_throughput = config["gpu_info"]["A100-80GB"]["tputs"][i][j]
                a100_cell_load = (
                    a100_cell_workload / a100_throughput if a100_throughput > 0 else 0
                )
                a100_load += a100_cell_load

                total_cell_workload = (
                    config["workload_distribution"][i][j] * config["total_request_rate"]
                )
                print(
                    f"Cell [{i}][{j}]: Total workload = {total_cell_workload:.1f} requests"
                )
                print(
                    f"  - A10G: Assigned workload = {a10g_cell_workload:.1f} ({a10g_cell_workload/total_cell_workload*100:.1f}%), "
                    + f"Throughput = {a10g_throughput:.1f}, Load = {a10g_cell_load:.3f}"
                )
                print(
                    f"  - A100-80GB: Assigned workload = {a100_cell_workload:.1f} ({a100_cell_workload/total_cell_workload*100:.1f}%), "
                    + f"Throughput = {a100_throughput:.1f}, Load = {a100_cell_load:.3f}"
                )

        print(f"\nTotal A10G load: {a10g_load:.3f} GPUs (allocated: {result['A10G']})")
        print(
            f"Total A100-80GB load: {a100_load:.3f} GPUs (allocated: {result['A100-80GB']})"
        )

        # Verify cost calculation
        expected_cost = (
            result["A10G"] * config["gpu_info"]["A10G"]["cost"]
            + result["A100-80GB"] * config["gpu_info"]["A100-80GB"]["cost"]
        )
        self.assertEqual(
            result["cost"],
            expected_cost,
            "Cost calculation should match expected value",
        )
        print("--------------------------------")

    def test_solver_with_infeasible_constraints(self):
        # Test case 3: With infeasible constraints
        with open(self.config_path, "r") as f:
            config = json.load(f)

        # Set very restrictive constraints that make the problem infeasible
        for gpu in config["gpu_info"]:
            config["gpu_info"][gpu]["max_count"] = 0

        solver = MelangeSolver(
            workload_distribution=config["workload_distribution"],
            total_request_rate=config["total_request_rate"],
            gpu_info=config["gpu_info"],
            slice_factor=config["slice_factor"],
        )

        result = solver.run(logs=True)
        self.assertIsNone(
            result, "Solver should return None for infeasible constraints"
        )
        print("\nInfeasible constraints test: solver correctly returned None")
        print("--------------------------------")

    def test_solver_with_only_a10g(self):
        """Test with only A10G GPUs available (max 10) and no A100-80GB"""
        with open(self.config_path, "r") as f:
            config = json.load(f)

        config["gpu_info"]["A10G"]["max_count"] = 10  # Can use up to 10 A10G
        config["gpu_info"]["A100-80GB"]["max_count"] = 0  # A100-80GB not available

        solver = MelangeSolver(
            workload_distribution=config["workload_distribution"],
            total_request_rate=config["total_request_rate"],
            gpu_info=config["gpu_info"],
            slice_factor=config["slice_factor"],
        )

        result = solver.run(logs=True)

        print("\nOnly A10G test results:")
        print(f"GPU allocation: {result}")

        # Calculate total workload
        total_workload = (
            sum(sum(row) for row in config["workload_distribution"])
            * config["total_request_rate"]
        )
        print(f"Total workload: {total_workload}")
        print(f"A10G count: {result['A10G']} (max: 10)")

        # Calculate loads for each cell (as the solver does)
        print("\nDetailed load analysis (as calculated by solver):")
        total_load = 0
        for i in range(len(config["workload_distribution"])):
            for j in range(len(config["workload_distribution"][i])):
                # Calculate workload for this specific cell
                cell_workload = (
                    config["workload_distribution"][i][j] * config["total_request_rate"]
                )

                # Calculate load for this specific cell (load = requests / throughput)
                throughput = config["gpu_info"]["A10G"]["tputs"][i][j]
                cell_load = (
                    cell_workload / throughput if throughput > 0 else float("inf")
                )
                total_load += cell_load

                print(
                    f"Cell [{i}][{j}]: Workload = {cell_workload:.1f} requests, "
                    + f"Throughput = {throughput:.1f} requests/GPU, "
                    + f"Load = {cell_load:.3f} GPUs"
                )

        print(f"\nTotal load across all cells: {total_load:.3f} GPUs")
        print(f"Required GPUs (rounded up): {math.ceil(total_load)}")

        # Verify that the allocated GPUs are sufficient to handle the total load
        self.assertGreaterEqual(
            result["A10G"],
            math.ceil(total_load),
            "Allocated GPUs should be sufficient to handle the total load",
        )

        # Verify cost calculation
        expected_cost = result["A10G"] * config["gpu_info"]["A10G"]["cost"]
        self.assertEqual(
            result["cost"],
            expected_cost,
            "Cost calculation should match expected value",
        )
        print("--------------------------------")

    def test_manual_allocation_with_three_a10g(self):
        """Test to verify if 3 A10G GPUs would be sufficient for the workload"""
        with open(self.config_path, "r") as f:
            config = json.load(f)

        # Manually set allocation to 3 A10G GPUs
        manual_allocation = {"A10G": 3, "A100-80GB": 0}

        print("\nManual allocation with 3 A10G GPUs:")
        print(f"GPU allocation: {manual_allocation}")

        # Calculate total workload
        total_workload = (
            sum(sum(row) for row in config["workload_distribution"])
            * config["total_request_rate"]
        )
        print(f"Total workload: {total_workload}")

        # Calculate loads for each cell (as the solver does)
        print("\nDetailed load analysis with 3 A10G GPUs:")
        total_load = 0
        is_sufficient = True

        for i in range(len(config["workload_distribution"])):
            for j in range(len(config["workload_distribution"][i])):
                # Calculate workload for this specific cell
                cell_workload = (
                    config["workload_distribution"][i][j] * config["total_request_rate"]
                )

                # Calculate load for this specific cell (load = requests / throughput)
                throughput = config["gpu_info"]["A10G"]["tputs"][i][j]
                cell_load = (
                    cell_workload / throughput if throughput > 0 else float("inf")
                )
                total_load += cell_load

                # Check if 3 GPUs are sufficient for this cell
                cell_sufficient = total_load <= 3
                if not cell_sufficient:
                    is_sufficient = False

                print(
                    f"Cell [{i}][{j}]: Workload = {cell_workload:.1f} requests, "
                    + f"Throughput = {throughput:.1f} requests/GPU, "
                    + f"Load = {cell_load:.3f} GPUs"
                )

        print(f"\nTotal load across all cells: {total_load:.3f} GPUs")
        print(f"Required GPUs (rounded up): {math.ceil(total_load)}")
        print(f"Is 3 A10G GPUs sufficient? {is_sufficient}")

        # Calculate expected cost
        expected_cost = 3 * config["gpu_info"]["A10G"]["cost"]
        print(f"Expected cost with 3 A10G GPUs: {expected_cost}")
        print("--------------------------------")

    def test_solver_with_only_a100(self):
        """Test with only A100-80GB GPUs available (max 3) and no A10G"""
        with open(self.config_path, "r") as f:
            config = json.load(f)

        # Set A100-80GB limit to 3 and A10G to be unavailable
        config["gpu_info"]["A100-80GB"]["max_count"] = 3  # Can use up to 3 A100s
        config["gpu_info"]["A10G"]["max_count"] = 0  # A10G not available

        solver = MelangeSolver(
            workload_distribution=config["workload_distribution"],
            total_request_rate=config["total_request_rate"],
            gpu_info=config["gpu_info"],
            slice_factor=config["slice_factor"],
        )

        result = solver.run(logs=True)

        # Verify the solution
        self.assertIsNotNone(
            result, "Solver should find a solution using only A100-80GB"
        )
        self.assertEqual(result["A10G"], 0, "Should not use any A10G GPUs")
        self.assertGreater(result["A100-80GB"], 0, "Should use some A100-80GB GPUs")
        self.assertLessEqual(
            result["A100-80GB"], 3, "Should not exceed A100-80GB limit of 3"
        )

        # Print detailed results
        print("\nOnly A100-80GB test results:")
        print(f"GPU allocation: {result}")

        total_workload = (
            sum(sum(row) for row in config["workload_distribution"])
            * config["total_request_rate"]
        )
        print(f"Total workload: {total_workload}")
        print(f"A100-80GB count: {result['A100-80GB']} (max: 3)")

        # Calculate loads for each cell (as the solver does)
        print("\nDetailed load analysis (as calculated by solver):")
        total_load = 0

        for i in range(len(config["workload_distribution"])):
            for j in range(len(config["workload_distribution"][i])):
                # Calculate workload for this specific cell
                cell_workload = (
                    config["workload_distribution"][i][j] * config["total_request_rate"]
                )

                # Calculate load for this specific cell (load = requests / throughput)
                throughput = config["gpu_info"]["A100-80GB"]["tputs"][i][j]
                cell_load = (
                    cell_workload / throughput if throughput > 0 else float("inf")
                )
                total_load += cell_load

                print(
                    f"Cell [{i}][{j}]: Workload = {cell_workload:.1f} requests, "
                    + f"Throughput = {throughput:.1f} requests/GPU, "
                    + f"Load = {cell_load:.3f} GPUs"
                )

        print(f"\nTotal load across all cells: {total_load:.3f} GPUs")
        print(f"Required GPUs (rounded up): {math.ceil(total_load)}")

        # Verify that the allocated GPUs are sufficient to handle the total load
        self.assertGreaterEqual(
            result["A100-80GB"],
            math.ceil(total_load),
            "Allocated GPUs should be sufficient to handle the total load",
        )

        # Verify cost calculation
        expected_cost = result["A100-80GB"] * config["gpu_info"]["A100-80GB"]["cost"]
        self.assertEqual(
            result["cost"],
            expected_cost,
            "Cost calculation should match expected value",
        )
        print("--------------------------------")


if __name__ == "__main__":
    unittest.main()
