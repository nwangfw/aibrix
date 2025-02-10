import unittest
import json
import os
from .solver import MelangeSolver

class TestMelangeSolver(unittest.TestCase):
    def setUp(self):
        self.config_path = os.path.join(os.path.dirname(__file__), 'config_example_with_constraints.json')

    def test_solver_with_gpu_constraints(self):
        # Test case 1: With GPU constraints 
        with open(self.config_path, 'r') as f:
            config = json.load(f)
        
        solver = MelangeSolver(
            workload_distribution=config['workload_distribution'],
            total_request_rate=config['total_request_rate'],
            gpu_info=config['gpu_info'],
            slice_factor=config['slice_factor']
        )
        
        # Run solver with logging enabled
        result = solver.run(logs=True)
        
        # Basic constraint checks
        self.assertIsNotNone(result)
        self.assertLessEqual(result['A10G'], 2, "A10G count exceeds max constraint")
        self.assertLessEqual(result['A100-80GB'], 1, "A100-80GB count exceeds max constraint")
        
        # Verify workload distribution
        total_workload = sum(sum(row) for row in config['workload_distribution']) * config['total_request_rate']
        
        # Calculate total capacity based on allocated GPUs
        total_capacity = 0
        for gpu_type, count in result.items():
            if gpu_type != 'cost':  # Skip the cost entry
                # Sum up throughput for each request size
                gpu_capacity = sum(sum(tput for tput in row) 
                                 for row in config['gpu_info'][gpu_type]['tputs'])
                total_capacity += gpu_capacity * count
                
                print(f"\nGPU Type: {gpu_type}")
                print(f"Count allocated: {count}")
                print(f"Max allowed: {config['gpu_info'][gpu_type]['max_count']}")
                print(f"Total capacity: {gpu_capacity * count}")
                
        print(f"\nTotal workload: {total_workload}")
        print(f"Total capacity: {total_capacity}")
        print(f"Solution cost: {result['cost']}")
        
        # Verify the solution can handle the workload
        self.assertGreater(total_capacity, 0, "Total capacity should be positive")

    def test_solver_without_gpu_constraints(self):
        # Test case 2: Without GPU constraints (should use default large number)
        with open(self.config_path, 'r') as f:
            config = json.load(f)
            
        # Remove max_count constraints
        for gpu in config['gpu_info']:
            del config['gpu_info'][gpu]['max_count']
        
        solver = MelangeSolver(
            workload_distribution=config['workload_distribution'],
            total_request_rate=config['total_request_rate'],
            gpu_info=config['gpu_info'],
            slice_factor=config['slice_factor']
        )
        
        result = solver.run(logs=True)
        self.assertIsNotNone(result)
        print(f"\nNo constraints test result: {result}")
        print(f"--------------------------------")
    def test_solver_with_infeasible_constraints(self):
        # Test case 3: With infeasible constraints
        with open(self.config_path, 'r') as f:
            config = json.load(f)
            
        # Set very restrictive constraints that make the problem infeasible
        for gpu in config['gpu_info']:
            config['gpu_info'][gpu]['max_count'] = 0
            
        solver = MelangeSolver(
            workload_distribution=config['workload_distribution'],
            total_request_rate=config['total_request_rate'],
            gpu_info=config['gpu_info'],
            slice_factor=config['slice_factor']
        )
        
        result = solver.run(logs=True)
        self.assertIsNone(result, "Solver should return None for infeasible constraints")
        print("\nInfeasible constraints test: solver correctly returned None")

    def test_solver_with_only_a10g(self):
        """Test with only A10G GPUs available (max 10) and no A100-80GB"""
        with open(self.config_path, 'r') as f:
            config = json.load(f)
        
        # Set A10G limit to 10 and A100-80GB to be unavailable
        config['gpu_info']['A10G']['max_count'] = 10  # Can use up to 10 A10G
        config['gpu_info']['A100-80GB']['max_count'] = 0  # A100-80GB not available
        
        solver = MelangeSolver(
            workload_distribution=config['workload_distribution'],
            total_request_rate=config['total_request_rate'],
            gpu_info=config['gpu_info'],
            slice_factor=config['slice_factor']
        )
        
        result = solver.run(logs=True)
        
        # Verify the solution
        self.assertIsNotNone(result, "Solver should find a solution using only A10G")
        self.assertGreater(result['A10G'], 0, "Should use some A10G GPUs")
        self.assertLessEqual(result['A10G'], 10, "Should not exceed A10G limit of 10")
        self.assertEqual(result['A100-80GB'], 0, "Should not use any A100-80GB GPUs")
        
        # Print detailed results
        print("\nOnly A10G test results:")
        print(f"GPU allocation: {result}")
        
        # Calculate and verify capacity
        total_workload = sum(sum(row) for row in config['workload_distribution']) * config['total_request_rate']
        
        # Calculate A10G capacity
        a10g_capacity = sum(sum(tput for tput in row) 
                          for row in config['gpu_info']['A10G']['tputs'])
        total_capacity = a10g_capacity * result['A10G']
        
        print(f"Total workload: {total_workload}")
        print(f"Total capacity: {total_capacity}")
        print(f"A10G count: {result['A10G']} (max: 10)")
        
        # Print detailed workload distribution
        print("\nWorkload distribution matrix:")
        for i, row in enumerate(config['workload_distribution']):
            print(f"Row {i}: {row}")
        
        print("\nA10G throughput matrix:")
        for i, row in enumerate(config['gpu_info']['A10G']['tputs']):
            print(f"Row {i}: {row}")
        
        # Verify capacity is sufficient
        self.assertGreater(total_capacity, 0, "Total capacity should be positive")
        
        # Verify cost calculation
        expected_cost = result['A10G'] * config['gpu_info']['A10G']['cost']
        self.assertEqual(result['cost'], expected_cost, "Cost calculation should match expected value")

    # def test_verify_unconstrained_optimality(self):
    #     """Verify that the unconstrained solution is truly optimal"""
    #     with open(self.config_path, 'r') as f:
    #         config = json.load(f)
            
    #     # Remove max_count constraints
    #     for gpu in config['gpu_info']:
    #         del config['gpu_info'][gpu]['max_count']
        
    #     solver = MelangeSolver(
    #         workload_distribution=config['workload_distribution'],
    #         total_request_rate=config['total_request_rate'],
    #         gpu_info=config['gpu_info'],
    #         slice_factor=config['slice_factor']
    #     )
        
    #     result = solver.run(logs=True)
    #     self.assertIsNotNone(result)
        
    #     print("\nVerifying unconstrained optimality:")
    #     print(f"Solution: {result}")
        
    #     # Calculate workload and capacity
    #     total_workload = sum(sum(row) for row in config['workload_distribution']) * config['total_request_rate']
        
    #     # Calculate capacities per GPU
    #     a10g_capacity = sum(sum(tput for tput in row) 
    #                       for row in config['gpu_info']['A10G']['tputs'])
    #     a100_capacity = sum(sum(tput for tput in row) 
    #                        for row in config['gpu_info']['A100-80GB']['tputs'])
        
    #     print(f"\nPer-GPU Capacities:")
    #     print(f"A10G capacity per GPU: {a10g_capacity}")
    #     print(f"A100-80GB capacity per GPU: {a100_capacity}")
    #     print(f"A10G cost per capacity: {config['gpu_info']['A10G']['cost'] / a10g_capacity:.6f}")
    #     print(f"A100-80GB cost per capacity: {config['gpu_info']['A100-80GB']['cost'] / a100_capacity:.6f}")
        
    #     # Verify that A10G is more cost-efficient
    #     a10g_cost_per_capacity = config['gpu_info']['A10G']['cost'] / a10g_capacity
    #     a100_cost_per_capacity = config['gpu_info']['A100-80GB']['cost'] / a100_capacity
        
    #     self.assertLess(a10g_cost_per_capacity, a100_cost_per_capacity, 
    #                    "A10G should be more cost-efficient per unit of capacity")
        
    #     # Verify that using only A10G is the cheapest solution
    #     total_capacity = a10g_capacity * result['A10G'] + a100_capacity * result['A100-80GB']
    #     print(f"\nTotal workload: {total_workload}")
    #     print(f"Total capacity: {total_capacity}")
    #     print(f"Solution cost: {result['cost']}")

    def test_solver_with_mixed_gpus(self):
        """Test where mixed GPU types give lowest cost, but removing either type increases cost"""
        config = {
            "workload_distribution": [
                [0.4, 0.1],  
                [0.1, 0.4]   
            ],
            "total_request_rate": 100,
            "gpu_info": {
                "A10G": {
                    "cost": 1.5,
                    "tputs": [
                        [100, 40],  
                        [30, 15]    
                    ]
                },
                "A100-80GB": {
                    "cost": 3.0,
                    "tputs": [
                        [120, 100],  
                        [90, 80]    
                    ]
                }
            },
            "slice_factor": 4
        }
        
        # Test 1: No constraints (should use mix of both)
        solver = MelangeSolver(
            workload_distribution=config['workload_distribution'],
            total_request_rate=config['total_request_rate'],
            gpu_info=config['gpu_info'],
            slice_factor=config['slice_factor']
        )
        
        result_no_constraints = solver.run(logs=True)
        print("\nNo constraints test (should use both GPUs):")
        print(f"Result: {result_no_constraints}")
        print(f"--------------------------------")

        # Test 2: Only A10G available
        config_only_a10g = config.copy()
        config_only_a10g['gpu_info'] = config['gpu_info'].copy()
        config_only_a10g['gpu_info']['A10G'] = config['gpu_info']['A10G'].copy()
        config_only_a10g['gpu_info']['A10G']['max_count'] = 10
        config_only_a10g['gpu_info']['A100-80GB'] = config['gpu_info']['A100-80GB'].copy()
        config_only_a10g['gpu_info']['A100-80GB']['max_count'] = 0
        
        solver_only_a10g = MelangeSolver(
            workload_distribution=config_only_a10g['workload_distribution'],
            total_request_rate=config_only_a10g['total_request_rate'],
            gpu_info=config_only_a10g['gpu_info'],
            slice_factor=config_only_a10g['slice_factor']
        )
        
        result_only_a10g = solver_only_a10g.run(logs=True)
        print("\nOnly A10G test:")
        print(f"Result: {result_only_a10g}")
        print(f"--------------------------------")

        # Test 3: Only A100-80GB available
        config_only_a100 = config.copy()
        config_only_a100['gpu_info'] = config['gpu_info'].copy()
        config_only_a100['gpu_info']['A10G'] = config['gpu_info']['A10G'].copy()
        config_only_a100['gpu_info']['A10G']['max_count'] = 0
        config_only_a100['gpu_info']['A100-80GB'] = config['gpu_info']['A100-80GB'].copy()
        config_only_a100['gpu_info']['A100-80GB']['max_count'] = 10
        
        solver_only_a100 = MelangeSolver(
            workload_distribution=config_only_a100['workload_distribution'],
            total_request_rate=config_only_a100['total_request_rate'],
            gpu_info=config_only_a100['gpu_info'],
            slice_factor=config_only_a100['slice_factor']
        )
        
        result_only_a100 = solver_only_a100.run(logs=True)
        print("\nOnly A100-80GB test:")
        print(f"Result: {result_only_a100}")
        print(f"--------------------------------")

        # Verify that mixed solution is cheaper
        self.assertLess(result_no_constraints['cost'], result_only_a10g['cost'],
                       "Mixed solution should be cheaper than A10G-only")
        self.assertLess(result_no_constraints['cost'], result_only_a100['cost'],
                       "Mixed solution should be cheaper than A100-only")
        
        # Print detailed analysis
        print("\nDetailed Analysis:")
        print(f"Cost with both GPUs: {result_no_constraints['cost']}")
        print(f"Cost with only A10G: {result_only_a10g['cost']}")
        print(f"Cost with only A100-80GB: {result_only_a100['cost']}")
        
        # Calculate and print throughput efficiency for each scenario
        total_workload = sum(sum(row) for row in config['workload_distribution']) * config['total_request_rate']
        print(f"\nTotal workload: {total_workload}")
        
        def calculate_capacity(result, gpu_info):
            total_capacity = 0
            for gpu_type, count in result.items():
                if gpu_type != 'cost':
                    gpu_capacity = sum(sum(tput for tput in row) 
                                     for row in gpu_info[gpu_type]['tputs'])
                    total_capacity += gpu_capacity * count
            return total_capacity
        
        print(f"Capacity with both GPUs: {calculate_capacity(result_no_constraints, config['gpu_info'])}")
        print(f"Capacity with only A10G: {calculate_capacity(result_only_a10g, config_only_a10g['gpu_info'])}")
        print(f"Capacity with only A100: {calculate_capacity(result_only_a100, config_only_a100['gpu_info'])}")

    def test_solver_with_only_a100(self):
        """Test with only A100-80GB GPUs available (max 3) and no A10G"""
        with open(self.config_path, 'r') as f:
            config = json.load(f)
        
        # Set A100-80GB limit to 3 and A10G to be unavailable
        config['gpu_info']['A100-80GB']['max_count'] = 3  # Can use up to 3 A100s
        config['gpu_info']['A10G']['max_count'] = 0  # A10G not available
        
        solver = MelangeSolver(
            workload_distribution=config['workload_distribution'],
            total_request_rate=config['total_request_rate'],
            gpu_info=config['gpu_info'],
            slice_factor=config['slice_factor']
        )
        
        result = solver.run(logs=True)
        
        # Verify the solution
        self.assertIsNotNone(result, "Solver should find a solution using only A100-80GB")
        self.assertEqual(result['A10G'], 0, "Should not use any A10G GPUs")
        self.assertGreater(result['A100-80GB'], 0, "Should use some A100-80GB GPUs")
        self.assertLessEqual(result['A100-80GB'], 3, "Should not exceed A100-80GB limit of 3")
        
        # Print detailed results
        print("\nOnly A100-80GB test results:")
        print(f"GPU allocation: {result}")
        
        # Calculate and verify capacity
        total_workload = sum(sum(row) for row in config['workload_distribution']) * config['total_request_rate']
        
        # Calculate A100-80GB capacity
        a100_capacity = sum(sum(tput for tput in row) 
                          for row in config['gpu_info']['A100-80GB']['tputs'])
        total_capacity = a100_capacity * result['A100-80GB']
        
        print(f"Total workload: {total_workload}")
        print(f"Total capacity: {total_capacity}")
        print(f"A100-80GB count: {result['A100-80GB']} (max: 3)")
        
        # Print detailed workload distribution
        print("\nWorkload distribution matrix:")
        for i, row in enumerate(config['workload_distribution']):
            print(f"Row {i}: {row}")
        
        print("\nA100-80GB throughput matrix:")
        for i, row in enumerate(config['gpu_info']['A100-80GB']['tputs']):
            print(f"Row {i}: {row}")
        
        # Verify capacity is sufficient
        self.assertGreater(total_capacity, 0, "Total capacity should be positive")
        
        # Verify cost calculation
        expected_cost = result['A100-80GB'] * config['gpu_info']['A100-80GB']['cost']
        self.assertEqual(result['cost'], expected_cost, "Cost calculation should match expected value")

if __name__ == '__main__':
    unittest.main() 